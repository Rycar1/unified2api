"""Admin endpoints for limits, diagnostics, capabilities and managed releases."""
import asyncio
import secrets
import httpx
from fastapi import HTTPException, Request
from admin.server import digest


def register_controls(app,body_json,store,api,log,controls,capabilities,releases):
    @app.api_route('/admin/api/unified/key-limits/{kid}',methods=['GET','PATCH'])
    async def key_limits(kid:str,req:Request):
        store.require_admin(req)
        if kid not in store.data['keys']:
            raise HTTPException(404,'密钥不存在')
        policy = controls.save(kid,await body_json(req)) if req.method=='PATCH' else controls.policy(kid)
        return {'policy':policy,'usage':controls.usage(kid)}

    @app.get('/admin/api/unified/diagnostics/{request_id}')
    async def detail(request_id:str,req:Request):
        store.require_admin(req)
        return log.detail(request_id)

    async def model_row(model):
        catalog = (await api.models())['data']
        row = next((row for row in catalog if row['id']==model),None)
        if not row:
            raise HTTPException(404,'模型不在当前目录中')
        return row

    @app.post('/admin/api/unified/capability-probe')
    async def probe(req:Request):
        store.require_admin(req)
        row = await model_row((await body_json(req)).get('model'))
        key = secrets.token_urlsafe(32)
        hashed = digest(key)
        with store.lock:
            store.test_keys.add(hashed)
        async def runner(path,body):
            provider,_,model = body['model'].partition('/')
            if provider not in {'trae','monkeycode','codebuddy','workbuddy','qoder','qoder-cn','antigravity','route'}:
                connection = api.connections.get(provider)
                try:
                    async with api.connections.client(connection.get('kind','openai')) as client:
                        response = await client.post(connection['base_url']+path.removeprefix('/v1'),
                            headers={'Authorization':'Bearer '+connection['key']},json={**body,'model':model})
                    return response.status_code,response.json()
                except (httpx.HTTPError,ValueError):
                    return 502,{}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url='http://probe',timeout=120) as client:
                try:
                    response = await client.post(path,headers={'Authorization':'Bearer '+key},json=body)
                    return response.status_code,response.json()
                except (httpx.HTTPError,ValueError):
                    return 502,{}
        try:
            result = await capabilities.probe(row,runner)
            return {'model':row['id'],'capabilities':result}
        finally:
            with store.lock:
                store.test_keys.discard(hashed)

    @app.api_route('/admin/api/unified/model-capabilities/{model:path}',methods=['GET','PATCH','DELETE'])
    async def model_capabilities(model:str,req:Request):
        store.require_admin(req)
        row = await model_row(model)
        if req.method=='PATCH':
            capabilities.save(model,await body_json(req))
        elif req.method=='DELETE':
            with store.lock:
                values = capabilities.config.get('model_capabilities',{})
                values.pop(model,None)
                capabilities.config.set('model_capabilities',values)
        return {'model':model,'capabilities':row['capabilities'] if model.startswith('route/') else capabilities.describe(row)}

    @app.get('/admin/api/unified/releases')
    async def release_status(req:Request):
        store.require_admin(req)
        return await asyncio.to_thread(releases.status)

    @app.post('/admin/api/unified/releases/check')
    async def check_release(req:Request):
        store.require_admin(req)
        return await asyncio.to_thread(releases.check)

    @app.post('/admin/api/unified/releases/{action}')
    async def run_release(action:str,req:Request):
        store.require_admin(req)
        body = await body_json(req)
        return await asyncio.to_thread(releases.start,action,body.get('revision'))

    @app.get('/admin/api/unified/releases/operations/{job_id}')
    async def release_progress(job_id:str,req:Request):
        ticket = req.headers.get('X-Release-Ticket')
        if ticket is None:
            store.require_admin(req)
        return releases.progress(job_id,ticket)
