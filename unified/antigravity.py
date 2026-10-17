"""Antigravity account pool. Protocol reference: agent2api; see THIRD_PARTY_NOTICES."""
import asyncio
import base64
import hashlib
import json
import os
import re
import secrets
import time
import copy
from collections import OrderedDict
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from admin.server import write_json
from .errors import safe_error_message
from .qoder_relay import QoderRelay


AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
PROD = 'https://cloudcode-pa.googleapis.com/v1internal'
DAILY = 'https://daily-cloudcode-pa.googleapis.com/v1internal'
SCOPES = ['openid', *['https://www.googleapis.com/auth/'+s for s in
    ('cloud-platform', 'userinfo.email', 'userinfo.profile', 'cclog', 'experimentsandconfigs')]]
FALLBACK = ['gemini-3-flash', 'gemini-2.5-flash', 'gemini-2.5-pro']
AAD = b'unified2api-antigravity-v1'


def oauth_application():
    client_id = os.getenv('ANTIGRAVITY_CLIENT_ID', '').strip()
    client_secret = os.getenv('ANTIGRAVITY_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        raise HTTPException(503, '请在服务端环境中配置 ANTIGRAVITY_CLIENT_ID 和 ANTIGRAVITY_CLIENT_SECRET')
    return client_id, client_secret


def credential(value):
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 8192: raise HTTPException(400, '凭据内容过长')
        if value.startswith('{'):
            try: value = json.loads(value)
            except ValueError: raise HTTPException(400, '请输入有效的 JSON 凭据') from None
        else:
            match = re.search(r'1//[^\s\"\x27,;}\]]+', value)
            value = {'refresh_token': match.group() if match else value.strip('\"\x27')}
    if not isinstance(value, dict): raise HTTPException(400, '请输入 Google refresh token 或 JSON 凭据')
    node = value.get('token') if isinstance(value.get('token'), dict) else value
    token = node.get('refresh_token') or node.get('refreshToken')
    if not isinstance(token, str) or not token.strip() or len(token) > 8192 or any(c.isspace() for c in token):
        raise HTTPException(400, '凭据需要有效的 refresh_token')
    project = value.get('project_id', value.get('projectId', node.get('project_id', '')))
    if not isinstance(project, str) or len(project) > 256 or any(c in project for c in '\r\n'):
        raise HTTPException(400, 'project_id 格式无效')
    return token.strip(), project.strip()


def parse_models(value):
    models = value.get('models', {})
    items = models.items() if isinstance(models, dict) else ((r.get('id'), r) for r in models if isinstance(r, dict)) if isinstance(models, list) else []
    rows = []
    for mid, info in items:
        if not isinstance(mid, str) or not mid.startswith('gemini-') or not isinstance(info, dict): continue
        if len(mid) > 200 or any(c.isspace() for c in mid): continue
        row = {'id': 'antigravity/'+mid, 'object': 'model', 'owned_by': 'antigravity',
               'provider_name': 'Antigravity', 'catalog_source': 'antigravity-remote',
               'vision': info.get('supportsImages', True) is True, 'tools': True,
               'reasoning': info.get('supportsThinking', False) is True}
        for source, target in [('maxTokens','context_window'), ('maxInputTokens','context_window'), ('maxOutputTokens','max_output_tokens')]:
            number = info.get(source)
            if type(number) is int and 0 < number <= 10**8: row[target] = number
        if row['reasoning']: row['reasoning_efforts'] = ['minimal','low','medium','high']
        rows.append(row)
    return rows[:500]


class AntigravityAccounts(QoderRelay):
    provider_label = 'Antigravity'

    def __init__(self, store, transport=None):
        super().__init__()
        self.store, self.transport = store, transport
        self.path = store.root / 'antigravity.enc.json'
        key_path = store.root / '.antigravity.key'
        if not key_path.exists():
            if self.path.exists(): raise RuntimeError('Antigravity encryption key is missing; restore the matching backup')
            try:
                with os.fdopen(os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), 'wb') as handle:
                    handle.write(secrets.token_bytes(32))
            except FileExistsError: pass
        self.cipher = AESGCM(key_path.read_bytes())
        self.rows = []
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding='utf-8'))
            self.rows = json.loads(self.cipher.decrypt(base64.b64decode(doc['nonce']), base64.b64decode(doc['data']), AAD))
        self.locks, self.active, self.flows = {}, {}, {}
        self.cursor = 0
        self.signatures = OrderedDict()

    def save(self):
        nonce = secrets.token_bytes(12)
        data = self.cipher.encrypt(nonce, json.dumps(self.rows, ensure_ascii=False, allow_nan=False).encode(), AAD)
        write_json(self.path, {'version': 1, 'nonce': base64.b64encode(nonce).decode(), 'data': base64.b64encode(data).decode()})

    def get(self, aid):
        row = next((r for r in self.rows if r['id'] == aid), None)
        if row is None: raise HTTPException(404, 'Antigravity 账号不存在')
        return row

    def public(self, row):
        status = 'paused' if not row['enabled'] else 'invalid' if row.get('invalid') else 'cooling' if row.get('cooldown', 0) > time.time() else 'ready'
        return {k: row[k] for k in ('id','name','enabled')} | {
            'uid': row.get('email') or row['id'], 'provider': 'antigravity', 'status': status,
            'expires_at': row.get('expires_at', 0)*1000, 'remaining': row.get('remaining'),
            'quota_unit': '%', 'quota_windows': row.get('quota_windows', []),
            'last_error': row.get('last_error', ''), 'inflight': self.active.get(row['id'], 0)}

    async def accounts(self): return [self.public(r) for r in self.rows]

    def client(self):
        options = {'timeout': httpx.Timeout(120, connect=15), 'follow_redirects': False, 'trust_env': False}
        if self.transport: options['transport'] = self.transport
        outbound = getattr(self, 'outbound', None)
        if outbound:
            options.update(outbound.options('antigravity', self.transport))
        elif not self.transport and os.getenv('ANTIGRAVITY_PROXY'):
            options['proxy'] = os.environ['ANTIGRAVITY_PROXY']
        return httpx.AsyncClient(**options)

    def headers(self, row):
        return {'Authorization': 'Bearer '+row['access_token'], 'User-Agent': 'Antigravity/4.3.0',
                'x-client-name': 'antigravity', 'x-client-version': '4.3.0', 'Content-Type': 'application/json'}

    def failure(self, response, row=None):
        try: data = response.json()
        except ValueError: data = {}
        if not isinstance(data,dict): data = {}
        if response.status_code == 400 and data.get('error') == 'invalid_grant':
            raise HTTPException(401, 'Antigravity 授权已失效，请重新登录或导入 refresh token')
        error = data.get('error', {})
        message = error.get('message') if isinstance(error, dict) else str(error)
        hidden = tuple(str((row or {}).get(k, '')) for k in ('access_token','refresh_token','client_id','client_secret'))
        detail = safe_error_message(message or 'Google 上游请求失败', response.status_code, hidden)
        raise HTTPException(response.status_code if response.status_code in (400,401,403,404,429) else 502,
                            'Antigravity '+str(detail)[:350])

    async def token_request(self, fields):
        client_id, client_secret = oauth_application()
        fields = {**fields, 'client_id': client_id, 'client_secret': client_secret}
        try:
            async with self.client() as client: response = await client.post(TOKEN_URL, data=fields)
        except httpx.HTTPError: raise HTTPException(502, '连接 Google 授权服务失败，请检查 Antigravity 出网代理') from None
        if response.status_code >= 300: self.failure(response, fields)
        try:
            value = response.json(); token = value['access_token']; expires = value.get('expires_in',3600)
            if not isinstance(token,str) or not token or type(expires) not in (int,float) or not 0 < expires <= 604800: raise ValueError()
            return value | {'expires_at': time.time()+expires}
        except (ValueError, KeyError, TypeError): raise HTTPException(502, 'Google 返回了无效的授权响应') from None

    async def refresh(self, row):
        value = await self.token_request({'grant_type': 'refresh_token', 'refresh_token': row['refresh_token']})
        row.update(access_token=value['access_token'], expires_at=value['expires_at'], invalid=False, last_error='')
        if isinstance(value.get('refresh_token'), str) and value['refresh_token']: row['refresh_token'] = value['refresh_token']
        return row['access_token']

    async def ensure_token(self, row, force=False, stale_token=None):
        async with self.locks.setdefault(row['id'], asyncio.Lock()):
            if self.get(row['id']) is not row: raise HTTPException(409, '账号已被替换，请重试')
            should_force = force and (stale_token is None or stale_token == row.get('access_token'))
            if should_force or not row.get('access_token') or row.get('expires_at', 0) < time.time()+120:
                try: await self.refresh(row)
                except HTTPException as exc:
                    if exc.status_code == 401: row.update(invalid=True, last_error=str(exc.detail)); self.save()
                    raise
                self.get(row['id']); self.save()
            return row['access_token']

    async def post(self, row, method, body, *, base=PROD):
        await self.ensure_token(row)
        try:
            async with self.client() as client:
                stale_token = row['access_token']
                response = await client.post(base+':'+method, headers=self.headers(row), json=body)
                if response.status_code == 401:
                    await self.ensure_token(row, force=True, stale_token=stale_token)
                    response = await client.post(base+':'+method, headers=self.headers(row), json=body)
        except httpx.HTTPError: raise HTTPException(502, '连接 Antigravity 失败，请检查出网代理') from None
        if response.status_code >= 300: self.failure(response, row)
        try:
            value = response.json()
            if not isinstance(value, dict): raise ValueError()
            return value
        except ValueError: raise HTTPException(502, 'Antigravity 返回了无效响应') from None

    async def discover(self, row):
        value = await self.post(row, 'loadCodeAssist', {'metadata': {'ideType': 'ANTIGRAVITY'}})
        project = value.get('cloudaicompanionProject')
        if not project:
            tier = next((t.get('id') for t in value.get('allowedTiers', []) if isinstance(t, dict) and t.get('isDefault')), 'legacy-tier')
            value = await self.post(row, 'onboardUser', {'tierId': tier, 'metadata': {'ideType': 'ANTIGRAVITY'}})
            project = (value.get('response') or {}).get('cloudaicompanionProject')
        if isinstance(project, dict): project = project.get('id')
        if isinstance(project, str) and project:
            row['project_id'] = project; self.save()

    async def refresh_models(self, row):
        value = await self.post(row, 'fetchAvailableModels', {'project': row['project_id']} if row.get('project_id') else {})
        models = parse_models(value)
        if not models: raise HTTPException(502, 'Antigravity 未返回可用的 Gemini 模型')
        self.get(row['id']); row.update(models=models, models_at=time.time()); self.save()
        return models

    async def import_account(self, body):
        token, project = credential(body.get('credential'))
        name = body.get('name', '')
        if not isinstance(name, str) or len(name.strip()) > 60: raise HTTPException(400, '备注不能超过 60 字符')
        row = {'id': hashlib.sha256(token.encode()).hexdigest()[:32], 'refresh_token': token,
               'project_id': project, 'name': name.strip() or 'Antigravity', 'enabled': True}
        await self.refresh(row)
        try:
            async with self.client() as client:
                response = await client.get('https://www.googleapis.com/oauth2/v2/userinfo', headers=self.headers(row))
            email = response.json().get('email') if response.status_code == 200 else None
            if isinstance(email, str) and 0 < len(email) <= 254: row['email'] = email
        except (httpx.HTTPError, ValueError): pass
        existing = next((r for r in self.rows if r['id']==row['id'] or row.get('email') and r.get('email')==row['email']), None)
        if existing:
            row['id'] = existing['id']; existing.update(row); row = existing
        else: self.rows.append(row)
        self.save()
        try:
            if not row.get('project_id'): await self.discover(row)
            await self.refresh_models(row)
        except HTTPException as exc: row['last_error'] = str(exc.detail); self.save()
        return self.public(row)

    async def models(self):
        catalog = {}
        for row in self.rows:
            if not row['enabled'] or row.get('invalid'): continue
            for model in row.get('models') or [{'id':'antigravity/'+m, 'object':'model', 'owned_by':'antigravity', 'catalog_source':'antigravity-fallback'} for m in FALLBACK]:
                catalog.setdefault(model['id'], model)
        return list(catalog.values())

    def update(self, aid, body):
        if set(body)-{'name','enabled'}: raise HTTPException(400, '仅支持修改备注和启用状态')
        row = self.get(aid)
        if 'name' in body:
            if not isinstance(body['name'],str) or len(body['name'].strip()) > 60: raise HTTPException(400, '备注不能超过 60 字符')
            row['name'] = body['name'].strip() or 'Antigravity'
        if 'enabled' in body:
            if type(body['enabled']) is not bool: raise HTTPException(400, '启用状态必须是布尔值')
            row['enabled'] = body['enabled']
        self.save(); return self.public(row)

    def delete(self, aid):
        row = self.get(aid); self.rows.remove(row); self.save(); return {'ok': True}

    async def operate(self, aid, action):
        row = self.get(aid)
        if action == 'refresh':
            await self.ensure_token(row, force=True)
            if not row.get('project_id'): await self.discover(row)
            await self.refresh_models(row)
            return {'ok': True, 'account': self.public(row)}
        if action != 'balance': raise HTTPException(400, 'Antigravity 不支持此操作')
        value = await self.post(row, 'retrieveUserQuotaSummary', {'project': row['project_id']} if row.get('project_id') else {}, base=DAILY)
        windows = []
        for group in value.get('groups', []) if isinstance(value.get('groups'), list) else []:
            if not isinstance(group, dict): continue
            for bucket in group.get('buckets', []) if isinstance(group.get('buckets'), list) else []:
                if not isinstance(bucket, dict): continue
                fraction = bucket.get('remainingFraction')
                if type(fraction) not in (float, int) or not 0 <= fraction <= 1: continue
                windows.append({'name': str(group.get('displayName') or 'Gemini')[:100], 'remaining': round(fraction*100,2), 'reset_at': str(bucket.get('resetTime') or '')[:60]})
        self.get(aid); row.update(quota_windows=windows, remaining=min((w['remaining'] for w in windows), default=None), quota_at=time.time())
        self.save(); return {'ok': True, 'remaining': row['remaining'], 'unit':'%', 'windows': windows, 'account': self.public(row)}

    async def maintain(self):
        while True:
            await asyncio.sleep(60)
            for row in list(self.rows):
                if not row['enabled'] or row.get('invalid'): continue
                try: await self.ensure_token(row)
                except HTTPException: pass

    def history_key(self, rid):
        from .diagnostics import CURRENT_TRACE
        trace = CURRENT_TRACE.get()
        return (trace.key_id if trace else '', rid)

    async def chat_chunks(self, chat):
        from .antigravity_protocol import request_envelope, gemini_frames
        from .diagnostics import CURRENT_TRACE, note_account
        chat = copy.deepcopy(chat)
        mid = chat['model']
        trace = CURRENT_TRACE.get()
        namespace = trace.key_id if trace else ''
        affinity = None
        now = time.time()
        while self.signatures and next(iter(self.signatures.values()))[0] < now-3600:
            self.signatures.popitem(last=False)
        for message in chat.get('messages', []):
            for call in message.get('tool_calls') or []:
                record = self.signatures.get((namespace,call.get('id')))
                if record:
                    if affinity and affinity != record[1]: raise HTTPException(400, '工具调用来自不同的 Antigravity 账号，请开始新会话')
                    affinity = record[1]
                    if record[2]: call['extra_content'] = {'google': {'thought_signature': record[2]}}
        candidates = [r for r in self.rows if r['enabled'] and not r.get('invalid')
            and r.get('model_cooldowns',{}).get(mid,0)<=now
            and (not r.get('models') or any(m['id']=='antigravity/'+mid for m in r['models']))
            and (not affinity or r['id']==affinity)]
        start = self.cursor % max(1,len(candidates)); self.cursor += 1
        candidates = candidates[start:]+candidates[:start]
        limit = trace.max_account_concurrency if trace else 2
        candidates = [r for r in candidates if self.active.get(r['id'],0)<limit]
        if trace and trace.smart: candidates.sort(key=lambda r:self.active.get(r['id'],0))
        if not candidates: raise HTTPException(503, 'Antigravity 没有支持此模型且空闲的可用账号；原工具会话需要原账号')
        for index, row in enumerate(candidates):
            emitted = False; aid = row['id']
            self.active[aid] = self.active.get(aid,0)+1
            note_account('antigravity',aid,row['name'])
            try:
                await self.ensure_token(row)
                if not row.get('project_id'): await self.discover(row)
                wire = dict(chat)
                aliases = {'gemini-3.1-pro-high':'gemini-pro-agent','gemini-3-pro-high':'gemini-pro-agent',
                           'gemini-2.5-flash-lite':'gemini-2.5-flash'}
                wire['model'] = aliases.get(mid, mid+'-tiered' if re.fullmatch(r'gemini-3\.[678]-flash',mid) else mid)
                envelope = request_envelope(wire, row.get('project_id',''), bool(row.get('email') and not row['email'].endswith(('@gmail.com','@googlemail.com'))))
                async with self.client() as client:
                    for attempt in range(2):
                        stale_token = row['access_token']
                        async with client.stream('POST', DAILY+':streamGenerateContent?alt=sse', headers=self.headers(row), json=envelope) as response:
                            if response.status_code == 401 and attempt == 0:
                                await self.ensure_token(row, force=True, stale_token=stale_token); continue
                            if response.status_code >= 300:
                                await response.aread(); self.failure(response,row)
                            async for frame in gemini_frames(response.aiter_lines()):
                                for choice in frame.get('choices',[]):
                                    for call in (choice.get('delta') or {}).get('tool_calls',[]):
                                        signature=((call.get('extra_content') or {}).get('google') or {}).get('thought_signature')
                                        self.signatures[(namespace,call['id'])]=(time.time(),aid,signature)
                                        while len(self.signatures)>2048: self.signatures.popitem(last=False)
                                emitted = True; yield frame
                            return
            except httpx.HTTPError:
                if emitted or index==len(candidates)-1: raise HTTPException(502, 'Antigravity 网络连接中断，请检查出网代理') from None
            except HTTPException as exc:
                if exc.status_code == 429 and row in self.rows:
                    row.setdefault('model_cooldowns',{})[mid] = time.time()+300; self.save()
                if emitted or affinity or index==len(candidates)-1 or exc.status_code in (400,404,422): raise
            finally:
                self.active[aid] = max(0,self.active.get(aid,0)-1)

    async def close(self):
        self.flows.clear(); self.history.clear(); self.signatures.clear()
