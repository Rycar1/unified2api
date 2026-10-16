"""Admin-only model identity and reliability controls."""
from fastapi import HTTPException, Request


def register_aliases(app,body_json,store,api):
    @app.get('/admin/api/unified/model-aliases')
    async def aliases(req:Request):
        store.require_admin(req)
        return {'aliases':api.aliases.rows()}

    @app.post('/admin/api/unified/model-aliases')
    async def add_alias(req:Request):
        store.require_admin(req)
        body=await body_json(req)
        return api.aliases.save(body,(await api.models())['data'])

    @app.api_route('/admin/api/unified/model-aliases/{mid:path}',methods=['PATCH','DELETE'])
    async def edit_alias(mid:str,req:Request):
        store.require_admin(req)
        if req.method=='DELETE': return api.aliases.delete(mid)
        body=await body_json(req)
        return api.aliases.save(body,(await api.models())['data'],mid)

    @app.get('/admin/api/unified/model-validation')
    async def validate(req:Request,model:str=''):
        store.require_admin(req)
        catalog=(await api.models())['data']
        try:
            target=api.aliases.resolve(model,catalog)
            return {'valid':any(r['id']==target for r in catalog),'model':model,'target':target,'suggestions':[]}
        except HTTPException as exc:
            return {'valid':False,'model':model,**(exc.detail if isinstance(exc.detail,dict) else {'message':exc.detail})}
