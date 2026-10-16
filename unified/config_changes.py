"""Journal only configuration mutations; restoring one entity preserves the rest."""
import asyncio
import copy
import json
import re
from fastapi import HTTPException,Request
from fastapi.responses import JSONResponse
from admin.server import write_json
from .error_diagnosis import error_body


class ConfigChanges:
    def __init__(self,history,config,api,automation,probes,credits):
        self.history,self.config,self.api=history,config,api
        self.store=api.buddy.state.store
        self.automation,self.probes,self.credits=automation,probes,credits
        self.lock=asyncio.Lock()

    def identify(self,path,method,body):
        base='/admin/api/unified/'
        if not path.startswith('/admin/api/') or method not in {'POST','PATCH','DELETE'}: return None
        for suffix,kind in [('network','network'),('automation','automation'),('probe-plans','probe_plans'),('credit-policy','credit_policy')]:
            if path==base+suffix and method=='PATCH': return kind,'settings'
        for suffix,kind in [('routes','route'),('connections','connection'),('model-aliases','alias')]:
            root=base+suffix
            if path==root and method=='POST' and isinstance(body.get('id'),str): return kind,body['id']
            if path.startswith(root+'/'):
                tail=path[len(root)+1:]
                if kind=='alias' and method in {'PATCH','DELETE'}: return kind,tail
                if '/' not in tail and method in {'PATCH','DELETE'}: return kind,tail
                if kind=='connection' and re.fullmatch(r'[^/]+/accounts(?:/[^/]+)?',tail): return kind,tail.split('/')[0]
        for prefix,kind in [(base+'key-limits/','key'),('/admin/api/keys/','key'),(base+'model-capabilities/','capability')]:
            if path.startswith(prefix) and method=='PATCH': return kind,path[len(prefix):]
        if path.startswith(base+'model-capabilities/') and method=='DELETE': return 'capability',path[len(base+'model-capabilities/'):]
        return None

    def snapshot(self,kind,entity):
        if kind=='route': return next((r for r in self.config.get('routes',[]) if r['id']==entity),None)
        if kind=='alias': return next((r for r in self.api.aliases.rows() if r['id']==entity),None)
        if kind=='network': return self.api.network.get()
        if kind=='automation': return self.automation.settings()
        if kind=='probe_plans': return self.probes.settings()
        if kind=='credit_policy': return self.credits.settings()
        if kind=='capability': return self.config.get('model_capabilities',{}).get(entity)
        if kind=='key':
            with self.store.lock:
                row=self.store.data['keys'].get(entity)
                return {'name':row['name'],'policy':self.api.controls.policy(entity)} if row else None
        if kind=='connection':
            with self.api.connections.lock: row=copy.deepcopy(self.api.connections.data.get(entity))
            if row:
                for account in row.get('accounts',[]): account.pop('balance',None)
            return row
        raise HTTPException(400,'不支持恢复此类配置')

    async def apply(self,kind,entity,target):
        if kind in {'route','alias'}:
            manager=self.api.routes if kind=='route' else self.api.aliases
            current=self.snapshot(kind,entity)
            if target is None:
                if current is not None: manager.delete(entity)
            else:
                catalog=(await self.api.models())['data']
                models={m['id'] for m in catalog if not m['id'].startswith('route/')} if kind=='route' else catalog
                manager.save(target,models,entity if current is not None else None)
        elif kind=='connection':
            connection=self.api.connections
            with connection.lock:
                data=copy.deepcopy(connection.data)
                if target is None: data.pop(entity,None)
                else:
                    target=connection.validate(copy.deepcopy(target),copy.deepcopy(target))
                    old=data.get(entity,{})
                    same_service=all(old.get(k)==target.get(k) for k in ('base_url','balance_scheme','balance_key','balance_user_id'))
                    old_accounts={a['id']:a for a in old.get('accounts',[])}
                    for account in target['accounts']:
                        previous=old_accounts.get(account['id'],{})
                        if same_service and all(account.get(k)==previous.get(k) for k in ('key','balance_key','balance_user_id')):
                            if 'balance' in previous: account['balance']=previous['balance']
                        else:
                            connection.cooldowns.pop((entity,account['id']),None)
                            connection.pool_errors.pop((entity,account['id']),None)
                    data[entity]=target
                write_json(connection.path,data);connection.data=data
        elif kind=='key':
            if target is None or entity not in self.store.data['keys']: raise HTTPException(400,'密钥已删除，无法从规则历史重新生成凭据')
            with self.store.lock:
                self.api.controls.save(entity,target['policy'])
                self.store.data['keys'][entity]['name']=target['name'];self.store.save()
        elif kind=='network': self.api.network.update(target)
        elif kind=='automation': self.automation.update(target)
        elif kind=='probe_plans':
            ids={r['id'] for r in (await self.api.models())['data']}
            if any(m not in ids for m in target['models']): raise HTTPException(400,'原巡检模型已不在目录中，请先调整模型')
            self.probes.update(target)
        elif kind=='credit_policy': self.credits.update(target)
        elif kind=='capability':
            if target is None:
                values=self.config.get('model_capabilities',{});values.pop(entity,None);self.config.set('model_capabilities',values)
            else: self.api.capabilities.save(entity,{k:v for k,v in target.items() if k!='checked_at'})
        else: raise HTTPException(400,'不支持恢复此类配置')

    async def restore(self,rid,body):
        async with self.lock:
            row,_=self.history.get(rid)
            current=self.snapshot(row['kind'],row['entity'])
            self.history.verify(body.get('fingerprint'),current)
            target=self.history.target(rid,body.get('side','before'))
            journal=self.history.reserve(row['kind'],row['entity'],current,'restore:'+str(rid))
            try: await self.apply(row['kind'],row['entity'],target)
            finally: self.history.complete(journal,self.snapshot(row['kind'],row['entity']))
            return {'ok':True,'kind':row['kind'],'entity':row['entity']}

    def register(self,app,body_json):
        @app.get('/admin/api/unified/config-history')
        async def versions(req:Request,limit:int=30,offset:int=0,kind:str=''):
            self.store.require_admin(req);return self.history.query(limit,offset,kind)
        @app.get('/admin/api/unified/config-history/{rid}')
        async def detail(rid:int,req:Request):
            self.store.require_admin(req)
            row,_=self.history.get(rid)
            return self.history.detail(rid,self.snapshot(row['kind'],row['entity']))
        @app.post('/admin/api/unified/config-history/{rid}/restore')
        async def restore(rid:int,req:Request):
            self.store.require_admin(req);return await self.restore(rid,await body_json(req))


class ConfigHistoryMiddleware:
    def __init__(self,app,changes): self.app,self.changes=app,changes

    async def __call__(self,scope,receive,send):
        path=scope.get('path','');method=scope.get('method','')
        if scope.get('type')!='http' or not path.startswith('/admin/api/') or method not in {'POST','PATCH','DELETE'}:
            return await self.app(scope,receive,send)
        # Restore has its own guarded journal and must not acquire this lock twice.
        if path.startswith('/admin/api/unified/config-history/'):
            return await self.app(scope,receive,send)
        parts=[];size=0
        # Only JSON mutation endpoints participate; login uploads and backups retain
        # their existing request handling and size limits.
        headers=dict(scope.get('headers',[]))
        if b'application/json' not in headers.get(b'content-type',b'') and method!='DELETE':
            return await self.app(scope,receive,send)
        while True:
            item=await receive()
            if item['type']=='http.disconnect': return
            parts.append(item.get('body',b''));size+=len(parts[-1])
            if size>8*1024*1024:
                return await JSONResponse({'detail':'配置请求超过 8 MB'},413)(scope,receive,send)
            if not item.get('more_body'): break
        raw=b''.join(parts)
        try: body=json.loads(raw)
        except (ValueError,UnicodeDecodeError): body={}
        body=body if isinstance(body,dict) else {}
        entity=self.changes.identify(path,method,body)
        replayed=False
        async def replay():
            nonlocal replayed
            if not replayed: replayed=True;return {'type':'http.request','body':raw,'more_body':False}
            return await receive()
        if not entity: return await self.app(scope,replay,send)
        try: self.changes.store.require_admin(Request(scope))
        except HTTPException as exc: return await JSONResponse(error_body(exc),exc.status_code)(scope,replay,send)
        async with self.changes.lock:
            before=self.changes.snapshot(*entity)
            journal=self.changes.history.reserve(*entity,before)
            events=[]
            async def capture(message): events.append(message)
            try: await self.app(scope,replay,capture)
            finally: self.changes.history.complete(journal,self.changes.snapshot(*entity))
            for event in events: await send(event)
