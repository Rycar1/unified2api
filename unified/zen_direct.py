"""Python Zen transport, based on zen-proxy's session/tool framing (MIT)."""
import copy
import hashlib
import hmac
import json
import secrets
import time
from collections import OrderedDict
from contextvars import ContextVar
from pathlib import Path

import httpx
from fastapi import HTTPException
from .errors import safe_error_message
from .qoder_relay import QoderRelay

NAMESPACE = ContextVar('zen_namespace', default=('', ''))
UPSTREAM = 'https://opencode.ai/inference/v1'
UA = 'opencode/1.18.30'
ROOT = Path(__file__).resolve().parent.parent / 'vendor/zen-proxy'


class ZenDirect(QoderRelay):
    provider_label = 'OpenCode Zen'

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.transport = None
        self.sessions = OrderedDict()
        self.bridge_tools = json.loads((ROOT/'agent-tools.json').read_text(encoding='utf-8'))['tools']
        self.identity_key = secrets.token_bytes(32)

    def client(self):
        outbound = self.runtime.outbound
        options = outbound.options(transport=self.transport) if outbound else {'trust_env':False}
        return httpx.AsyncClient(transport=self.transport, follow_redirects=False,
            timeout=httpx.Timeout(None, read=180, connect=20), **options)

    def history_key(self, rid):
        return (*NAMESPACE.get(), rid)

    def session_id(self):
        namespace = NAMESPACE.get()
        now = time.time()
        hit = self.sessions.get(namespace)
        if not hit or now-hit[0] >= 1800:
            stamp = (~(int(now*1000)*4096+1)) & ((1 << 48)-1)
            tail = ''.join(secrets.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz') for _ in range(14))
            hit = (now, 'ses_'+format(stamp,'012x')+tail)
            self.sessions[namespace] = hit
        self.sessions.move_to_end(namespace)
        while len(self.sessions)>2048: self.sessions.popitem(last=False)
        return hit[1]

    async def relay(self, cid, path, body, scope, receive, send):
        if path not in {'/v1/chat/completions','/v1/responses','/v1/messages'}:
            raise HTTPException(400, 'Zen 直连不支持原生远程压缩')
        headers = dict(scope.get('headers',[]))
        caller = headers.get(b'authorization',headers.get(b'x-api-key',b''))
        namespace = hmac.new(self.identity_key,cid.encode()+b'\0'+caller,hashlib.sha256).hexdigest()
        model = json.loads(body).get('model','')
        token = NAMESPACE.set((namespace,model))
        try:
            return await super().relay(path,body,scope,receive,send)
        finally:
            NAMESPACE.reset(token)

    async def models(self):
        async with self.client() as client:
            response = await client.get(UPSTREAM+'/models',headers={'User-Agent':UA})
            if response.status_code != 200:
                raise HTTPException(502,'OpenCode 模型目录读取失败')
            data = response.json()
        # This adapter uses Chat upstream; native Responses/System One models have separate contracts.
        models = [m['id'] for m in data.get('data',[]) if isinstance(m,dict) and isinstance(m.get('id'),str)
            and not m['id'].startswith(('jev-','muse-spark-','gpt-','grok-','claude-','gemini-'))
            and (m['id'].endswith('-free') or m['id']=='big-pickle' or self.runtime.official_key)]
        return list(dict.fromkeys(models))

    async def chat_chunks(self, chat):
        wire = copy.deepcopy(chat)
        model = wire.get('model','')
        if model.startswith(('jev-','muse-spark-','gpt-','grok-','claude-','gemini-')):
            raise HTTPException(400,'该 OpenCode 模型使用独立协议，请配置普通自定义服务接入')
        tools = wire.get('tools') or []
        if not isinstance(tools,list) or any(not isinstance(t,dict) or t.get('type')!='function'
                or not isinstance(t.get('function'),dict) or not isinstance(t['function'].get('name'),str) for t in tools):
            raise HTTPException(400,'Zen 直连仅支持 function 工具定义')
        allowed = {t['function']['name'] for t in tools}
        wire['tools'] = tools + [t for t in self.bridge_tools if t['function']['name'] not in allowed]
        wire['stream'] = True
        if not tools: wire['tool_choice'] = 'none'
        if 'max_completion_tokens' in wire and 'max_tokens' not in wire:
            wire['max_tokens'] = wire.pop('max_completion_tokens')
        session = self.session_id()
        official_key = self.runtime.official_key
        headers = {'Authorization':'Bearer '+(official_key or 'public'),'User-Agent':UA,
            'Content-Type':'application/json','Accept':'application/json, text/event-stream',
            'x-opencode-session':session,'x-opencode-session-id':session}
        finished = False
        produced = False
        calls = {}
        try:
            async with self.client() as client:
                async with client.stream('POST',UPSTREAM+'/chat/completions',json=wire,headers=headers) as response:
                    if response.status_code != 200:
                        from .custom import _bounded_body
                        detail = await _bounded_body(response)
                        raise HTTPException(response.status_code,safe_error_message(detail,response.status_code,(official_key,)))
                    async for line in response.aiter_lines():
                        if len(line)>2*1024*1024: raise HTTPException(502,'Zen 流式事件超出大小限制')
                        if not line.startswith('data:'): continue
                        raw = line[5:].strip()
                        if raw=='[DONE]': break
                        try: frame = json.loads(raw)
                        except ValueError: raise HTTPException(502,'Zen 返回了无效的流式事件') from None
                        if not isinstance(frame,dict): raise HTTPException(502,'Zen 返回了无效的流式事件')
                        if frame.get('error'):
                            raise HTTPException(502,safe_error_message(json.dumps(frame['error']),502,(official_key,)))
                        if frame.get('model') and frame['model']!=model:
                            raise HTTPException(502,'Zen 返回的模型与请求不一致；未自动切换模型')
                        for choice in frame.get('choices',[]):
                            delta = choice.get('delta') or {}
                            produced |= bool(delta.get('content'))
                            for call in delta.get('tool_calls') or []:
                                fn = call.get('function') or {}
                                index = call.get('index',0)
                                name = calls.get(index,'')+fn.get('name','')
                                calls[index] = name
                                if name and not any(candidate.startswith(name) for candidate in allowed):
                                    raise HTTPException(502,'Zen 请求了客户端未声明的工具；没有执行或隐藏该调用')
                                produced = True
                            if choice.get('finish_reason'):
                                if not produced or any(name not in allowed for name in calls.values()):
                                    raise HTTPException(502,'Zen 未返回正文或完整的客户端工具调用')
                                finished = True
                        yield frame
            if not finished: raise HTTPException(502,'Zen 流提前结束，缺少 finish_reason')
            if not produced: raise HTTPException(502,'Zen 未返回正文或有效工具调用')
            if any(name not in allowed for name in calls.values()):
                raise HTTPException(502,'Zen 工具调用名称不完整')
        except httpx.HTTPError:
            raise HTTPException(502,'Zen 直连网络中断，请稍后重试') from None
