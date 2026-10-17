"""Model capabilities with explicit provenance, overrides and synthetic probes."""
import asyncio
import json
import secrets
import time
from fastapi import HTTPException

PROTOCOL_VALUES = {'native','converted','supported','unsupported','unknown'}
FIELDS = {'vision','tools','compact','context_window','max_output_tokens','reasoning','reasoning_efforts','protocols'}


class ModelCapabilities:
    def __init__(self,config,connections=None):
        self.config = config
        self.connections = connections
        self.lock = asyncio.Lock()

    def is_runtime(self,row):
        prefix = row['id'].partition('/')[0]
        return row.get('integration') == 'opencode_runtime' or (
            self.connections is not None and self.connections.data.get(prefix,{}).get('kind') == 'opencode_runtime')

    def describe(self,row):
        prefix = row['id'].partition('/')[0]
        converted = prefix in {'codebuddy','workbuddy','qoder','qoder-cn','antigravity'}
        chat_only = prefix in {'trae','monkeycode'}
        protocols = {'chat':'supported' if converted or chat_only else 'unknown',
                     'responses':'converted' if converted else 'unsupported' if chat_only else 'unknown',
                     'messages':'converted' if converted else 'unsupported'}
        result = {'vision':None,'tools':None,'context_window':None,'compact':False if converted or chat_only else None,
                  'protocols':protocols,'sources':{'protocols':'gateway','compact':'gateway' if converted or chat_only else 'unknown'}}
        runtime = self.is_runtime(row)
        if runtime:
            result.update(compact=False, tools=None,
                protocols={'chat':'supported','responses':'converted','messages':'converted'})
            result['sources'].update(protocols='gateway',compact='gateway')
        for field in ('vision','tools','reasoning'):
            if type(row.get(field)) is bool:
                result[field] = row[field]
                result['sources'][field] = row.get('catalog_source','catalog')
        for field in ('context_window','max_output_tokens'):
            result.setdefault(field,None)
            if type(row.get(field)) is int and row[field]>0:
                result[field] = row[field]
                result['sources'][field] = row.get('limits_source',row.get('catalog_source','catalog'))
        result.setdefault('reasoning',None)
        result['reasoning_efforts'] = list(row.get('reasoning_efforts') or [])
        for name,source in [('detected_capabilities','probe'),('model_capabilities','manual')]:
            saved = self.config.get(name,{}).get(row['id'],{})
            for field in FIELDS:
                if field in saved:
                    result[field] = {**result[field],**saved[field]} if field=='protocols' else saved[field]
                    result['sources'][field] = source
            if saved.get('checked_at'):
                result['checked_at'] = saved['checked_at']
        if runtime:
            result.update(compact=False, protocols={'chat':'supported','responses':'converted','messages':'converted'})
            result['sources'].update(protocols='gateway',compact='gateway')
        return result

    def save(self,model,body):
        if not isinstance(body,dict) or set(body)-FIELDS:
            raise HTTPException(400,'模型能力包含不支持的字段')
        for field in ('vision','tools','compact','reasoning'):
            if field in body and body[field] is not None and type(body[field]) is not bool:
                raise HTTPException(400,'能力状态应为支持、不支持或未知')
        for field in ('context_window','max_output_tokens'):
            value = body.get(field)
            if value is not None and (type(value) is not int or not 1<=value<=10**8):
                raise HTTPException(400,'Token 上限必须是正整数或未知')
        efforts=body.get('reasoning_efforts',[])
        if not isinstance(efforts,list) or len(efforts)>12 or any(not isinstance(item,str) or not item or len(item)>24 for item in efforts):
            raise HTTPException(400,'思考档位格式无效')
        protocols = body.get('protocols',{})
        if model.startswith('route/'):
            raise HTTPException(400,'路由能力由目标决定，请修改目标模型的能力标记')
        if not isinstance(protocols,dict) or set(protocols)-{'chat','responses','messages'} or any(value not in PROTOCOL_VALUES for value in protocols.values()):
            raise HTTPException(400,'协议能力格式不正确')
        if protocols.get('messages','unsupported')!='unsupported':
            raise HTTPException(400,'此接口不支持手动开启原生 Messages')
        if model.partition('/')[0] in {'trae','monkeycode','codebuddy','workbuddy','qoder','qoder-cn'} or self.is_runtime({'id':model}):
            expected = self.describe({'id':model})['protocols']
            if any(protocols.get(key,expected[key])!=expected[key] for key in protocols) or body.get('compact') is True:
                raise HTTPException(400,'内置平台的协议由适配器决定，不能用标签开启未实现的原生协议或压缩')
        with self.config.store.lock:
            entries = self.config.get('model_capabilities',{})
            entries[model] = {**entries.get(model,{}),**body}
            self.config.set('model_capabilities',entries)
        return self.describe({'id':model})

    def route(self,row,targets,catalog):
        result = self.describe(row)
        children = [catalog.get(target) or self.describe({'id':target}) for target in targets]
        for protocol in ('chat','responses','messages'):
            values = [child['protocols'].get(protocol,'unknown') for child in children]
            usable = [value for value in values if value!='unsupported']
            value = 'unsupported' if not usable else 'unknown' if 'unknown' in usable else 'native' if all(value=='native' for value in usable) else 'converted' if 'converted' in usable else 'supported'
            result['protocols'][protocol] = value
        result['compact'] = any(child.get('compact') is True for child in children) if children else False
        for field in ('vision','tools'):
            values = [child.get(field) for child in children]
            result[field] = True if values and all(value is True for value in values) else False if False in values else None
        for field in ('context_window','max_output_tokens'):
            limits = [child.get(field) for child in children]
            result[field] = min(limits) if limits and all(limits) else None
        reasoning=[child.get('reasoning') for child in children]
        result['reasoning']=True if reasoning and all(value is True for value in reasoning) else False if reasoning and all(value is False for value in reasoning) else None
        efforts=[set(child.get('reasoning_efforts') or []) for child in children]
        common=set.intersection(*efforts) if efforts else set()
        result['reasoning_efforts']=[item for item in ('minimal','low','medium','high','xhigh','max') if item in common]
        result['sources'] = {field:'route targets' for field in FIELDS}
        return result

    async def probe(self,row,runner):
        if self.lock.locked():
            raise HTTPException(409,'正在检测模型能力，请稍后重试')
        async with self.lock:
            marker = 'unified-probe-'+secrets.token_hex(6)
            body = {'model':row['id'],'stream':False,'max_tokens':64,
                    'messages':[{'role':'user','content':marker}],
                    'tools':[{'type':'function','function':{'name':'unified_probe','description':'Return the exact supplied value.',
                              'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}}],
                    'tool_choice':{'type':'function','function':{'name':'unified_probe'}}}
            detected = {'checked_at':int(time.time()),'tools':None}
            status,value = await runner('/v1/chat/completions',body)
            if status==200 and isinstance(value,dict):
                choices = value.get('choices') or []
                for choice in choices if isinstance(choices,list) else []:
                    for call in (choice.get('message') or {}).get('tool_calls') or []:
                        fn = call.get('function') or {}
                        try:
                            valid = fn.get('name')=='unified_probe' and json.loads(fn.get('arguments',''))=={'value':marker}
                        except (ValueError,TypeError):
                            valid = False
                        if valid:
                            detected['tools'] = True
            # Conversion is known from the installed adapter. A 200 is insufficient
            # to claim native Responses or real compaction.
            if not self.is_runtime(row) and row['id'].partition('/')[0] not in {'trae','monkeycode','codebuddy','workbuddy','qoder','qoder-cn','antigravity','route'}:
                status,value = await runner('/v1/responses',{'model':row['id'],'input':marker,'stream':False,'max_output_tokens':16})
                native = status==200 and isinstance(value,dict) and value.get('object')=='response' and value.get('status')=='completed' and isinstance(value.get('output'),list)
                if native:
                    detected['protocols'] = {'responses':'native'}
                    status,value = await runner('/v1/responses/compact',{'model':row['id'],'input':[{'role':'user','content':marker}]})
                    output = value.get('output',[]) if isinstance(value,dict) else []
                    detected['compact'] = status==200 and isinstance(output,list) and len(output)==1 and isinstance(output[0],dict) and output[0].get('type')=='compaction' and isinstance(output[0].get('encrypted_content'),str) and bool(output[0]['encrypted_content'])
                elif status in {404,405}:
                    detected['protocols'] = {'responses':'unsupported'}
            with self.config.store.lock:
                entries = self.config.get('detected_capabilities',{})
                entries[row['id']] = detected
                self.config.set('detected_capabilities',entries)
            return self.describe(row)
