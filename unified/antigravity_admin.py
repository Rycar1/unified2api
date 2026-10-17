"""Admin account management and one-use, owner-bound Google OAuth flows."""
import asyncio
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from admin.server import COOKIE, digest
from .antigravity import AUTH_URL, SCOPES, oauth_application


def register(app, pool, body_json):
    base = '/admin/api/unified/antigravity'

    def owner(fid, req):
        pool.store.require_admin(req)
        flow = pool.flows.get(fid)
        if not flow or flow['owner'] != digest(req.cookies.get(COOKIE,'')):
            raise HTTPException(404, '登录会话不存在')
        if flow['expires'] < time.time():
            pool.flows.pop(fid,None); raise HTTPException(410, '登录会话已过期，请重新开始')
        return flow

    @app.api_route(base+'/accounts', methods=['GET','POST'])
    async def accounts(req: Request):
        pool.store.require_admin(req)
        if req.method == 'GET': return {'accounts': await pool.accounts()}
        return await pool.import_account(await body_json(req))

    @app.api_route(base+'/accounts/{aid}', methods=['PATCH','DELETE'])
    async def account(aid: str, req: Request):
        pool.store.require_admin(req)
        return pool.delete(aid) if req.method=='DELETE' else pool.update(aid,await body_json(req))

    @app.post(base+'/accounts/{aid}/{action}')
    async def operate(aid: str, action: str, req: Request):
        pool.store.require_admin(req)
        return await pool.operate(aid,action)

    @app.post(base+'/login')
    async def start(req: Request):
        pool.store.require_admin(req)
        client_id, _ = oauth_application()
        body = await body_json(req)
        name = body.get('name','')
        if not isinstance(name,str) or len(name.strip())>60: raise HTTPException(400,'备注不能超过 60 字符')
        pool.flows = {fid:f for fid,f in pool.flows.items() if f['expires']>time.time()}
        if len(pool.flows)>=50: raise HTTPException(429,'登录会话过多，请稍后重试')
        fid=secrets.token_urlsafe(32)
        local=req.url.hostname in {'localhost','127.0.0.1','::1'}
        port=req.url.port or (443 if req.url.scheme=='https' else 80) if local else 8080
        redirect=f'http://localhost:{port}'+base+'/callback'
        pool.flows[fid]={'owner':digest(req.cookies.get(COOKIE,'')), 'name':name.strip(),
                        'expires':time.time()+600, 'state':'pending','redirect':redirect,'lock':asyncio.Lock()}
        url=AUTH_URL+'?'+urlencode({'client_id':client_id,
            'redirect_uri':redirect,'response_type':'code','scope':' '.join(SCOPES),
            'state':fid,'access_type':'offline','prompt':'consent'})
        return {'id':fid,'url':url,'manual_callback':not local}

    async def finish(fid, code, error=False):
        flow=pool.flows.get(fid)
        if not flow or flow['expires']<time.time(): raise HTTPException(400,'授权状态无效或已过期')
        async with flow['lock']:
            if flow['state']!='pending': raise HTTPException(409,'此授权回调已使用')
            flow['state']='validating'
            try:
                if error or not code or len(code)>8192: raise HTTPException(400,'Google 授权未完成，请重新登录')
                tokens=await pool.token_request({'grant_type':'authorization_code','code':code,'redirect_uri':flow['redirect']})
                if not tokens.get('refresh_token'): raise HTTPException(400,'Google 未返回 refresh token，请重新同意授权')
                account=await pool.import_account({'credential':{'refresh_token':tokens['refresh_token']},'name':flow['name']})
                flow.update(state='success',account_id=account['id'],message='账号已添加')
            except HTTPException as exc:
                flow.update(state='failed',message=str(exc.detail)); raise

    @app.get(base+'/callback')
    async def callback(state: str='', code: str='', error: str=''):
        await finish(state,code,bool(error))
        return HTMLResponse('<!doctype html><meta charset="utf-8"><title>Antigravity</title><p>授权成功，返回 Unified 控制台即可。</p>',
            headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','Content-Security-Policy':"default-src 'none'"})

    @app.api_route(base+'/login/{fid}', methods=['GET','DELETE'])
    async def poll(fid: str, req: Request):
        flow=owner(fid,req)
        if req.method=='DELETE': pool.flows.pop(fid,None); return {'ok':True}
        return {key:flow[key] for key in ('state','message','account_id') if key in flow}

    @app.post(base+'/login/{fid}/callback')
    async def manual(fid: str, req: Request):
        flow=owner(fid,req)
        body=await body_json(req); value=body.get('url')
        if not isinstance(value,str) or len(value)>16384: raise HTTPException(400,'请粘贴完整授权回调地址')
        url=urlsplit(value); expected=urlsplit(flow['redirect']); query=parse_qs(url.query)
        if (url.scheme,url.netloc,url.path)!=(expected.scheme,expected.netloc,expected.path) or query.get('state')!=[fid]:
            raise HTTPException(400,'回调地址与本次授权不匹配')
        await finish(fid,query.get('code',[''])[0],bool(query.get('error')))
        return {'state':flow['state'],'account_id':flow.get('account_id')}
