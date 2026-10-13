"""Admin-only benefits, quota detail and provider settings; no conversation reads."""
import asyncio
import importlib
import math
from pathlib import Path
import sys
import time
from urllib.parse import quote
from fastapi import HTTPException,Request
from .errors import safe_error_message

SENSITIVE={'accesstoken','refreshtoken','personaltoken','token','apikey','password','panelpassword',
           'authorization','cookie','cookies','credential','credentials','auth','headers'}
SETTINGS={'reserve_credits','daily_token_limit','daily_credit_limit','model_daily_token_limit',
          'local_web_tools','pricing_enabled','remaining_priority_enabled'}


def metadata(value,secrets=(),depth=0):
    if depth>12: return None
    if isinstance(value,dict):
        return {str(k):metadata(v,secrets,depth+1) for k,v in value.items()
                if str(k).replace('_','').replace('-','').lower() not in SENSITIVE}
    if isinstance(value,list): return [metadata(v,secrets,depth+1) for v in value[:1000]]
    if isinstance(value,str):
        for secret in sorted((str(s) for s in secrets if s and len(str(s))>=4),key=len,reverse=True):
            value=value.replace(secret,'[redacted]')
        return value[:2000]
    if isinstance(value,float) and not math.isfinite(value): return None
    return value if value is None or isinstance(value,(int,float,bool)) else str(value)[:200]


class TaskCenter:
    def __init__(self,config,store,pool,qoder,workbuddy,native=None):
        self.config,self.store,self.pool,self.qoder,self.workbuddy=config,store,pool,qoder,workbuddy
        self.native=native
        self.locks={}

    def domestic(self,aid):
        source=Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub'
        if str(source) not in sys.path: sys.path.insert(0,str(source))
        accounts=importlib.import_module('wb_accounts')
        item,_=self.pool.snapshot(aid)
        manager=self.store.manager_for(aid,item)
        headers=manager.get_headers()
        document=manager._session()
        uid=str((document.get('account') or {}).get('uid') or aid)
        account=accounts.Account({'uid':uid,'realm':'cn','nickname':item.get('name') or uid,
            'accessToken':(document.get('auth') or {}).get('accessToken') or ''})
        account.headers=lambda purpose='chat':dict(manager.get_headers())
        def refresh():
            result=self.pool.operate(aid,'status')
            account.credits={'remain':result.get('remaining')}
            return result
        account.fetch_credits=refresh
        return account

    async def view(self,provider,aid):
        if provider in {'qoder','qoder-cn','qoder_cn'}:
            account=self.qoder.get(aid)
            async with self.qoder.locks.setdefault(aid,asyncio.Lock()):
                await self.qoder.transport.refresh(account)
                tasks=importlib.import_module('qoder_tasks')
                rows,summary=await asyncio.to_thread(tasks.fetch_task_view,account)
            return metadata({'provider':provider,'account_id':aid,'tasks':rows,'summary':summary},
                (account.access_token,account.refresh_token,account.personal_token))
        if provider=='codebuddy':
            account=await asyncio.to_thread(self.domestic,aid)
            tasks=importlib.import_module('wb_tasks')
            rows,summary=await asyncio.gather(asyncio.to_thread(tasks.fetch_growth_tasks,account),
                                            asyncio.to_thread(tasks.fetch_growth_summary,account))
            return metadata({'provider':provider,'account_id':aid,'tasks':rows,'summary':summary},(account.access_token,))
        if provider in {'workbuddy','workbuddy_intl'}:
            await self.workbuddy.account(aid)
            return metadata(await self.workbuddy.hub('GET','/tasks?realm=intl&uid='+quote(aid,safe='')),
                            (self.workbuddy.api_key,self.workbuddy.admin_key,self.workbuddy.panel_token))
        raise HTTPException(400,'该平台暂无额外福利任务，请使用账号行的签到')

    async def run(self,provider,aid,action):
        async with self.locks.setdefault(provider+':'+aid,asyncio.Lock()):
            if provider in {'qoder','qoder-cn','qoder_cn'}:
                if action not in {'checkin','benefits','pro_claim'}: raise HTTPException(400,'不支持的任务')
                result=await self.qoder.operate(aid,action)
            elif provider=='codebuddy':
                if action=='checkin': result=await asyncio.to_thread(self.pool.operate,aid,'checkin')
                else:
                    names={'benefits':'run_growth_tasks','growth':'run_growth_tasks','streak':'run_streak_bonus','travel':'do_cat_travel'}
                    if action not in names: raise HTTPException(400,'不支持的任务')
                    account=await asyncio.to_thread(self.domestic,aid)
                    tasks=importlib.import_module('wb_tasks')
                    result=await asyncio.to_thread(getattr(tasks,names[action]),account)
                    result=metadata(result,(account.access_token,))
            elif provider in {'workbuddy','workbuddy_intl'}:
                if action not in {'checkin','daily_activity'}: raise HTTPException(400,'国际站仅支持每日活跃')
                result=await self.workbuddy.operate(aid,'checkin')
            else: raise HTTPException(400,'该平台暂无额外福利任务')
            result=metadata(result)
            result['message']=result.get('message') or result.get('msg') or '；'.join(result.get('logs',[])[-2:]) or '操作已执行'
            result.setdefault('state','completed' if result.get('ok') else 'failed')
            if not result.pop('_history_recorded',False): self.record(provider,aid,action,result)
            return result

    def record(self,provider,aid,action,result):
        row={'provider':provider,'account_id':aid,'action':action,'created':int(time.time()),
             **{k:result.get(k) for k in ('ok','state','earned_credit','remaining','message')}}
        with self.store.lock:
            history=self.config.get('activity_history',[])
            self.config.set('activity_history',(history+[metadata(row)])[-1000:])

    async def quota(self,provider,aid):
        if provider in {'trae','monkeycode'} and self.native:
            if provider=='trae':
                code,result=await self.native.request('POST','/admin/api/accounts/'+quote(aid,safe='')+'/balance',b'{}')
                if code>=400: raise HTTPException(code,'TRAE 余额查询失败，请检查账号状态')
                return metadata(result)
            code,_=await self.native.request('POST','/monkey/admin/accounts/'+quote(aid,safe='')+'/refresh',b'{}')
            if code>=400: raise HTTPException(code,'MonkeyCode 余额查询失败，请检查账号状态')
            code,result=await self.native.request('GET','/monkey/admin/accounts')
            row=next((r for r in result.get('accounts',[]) if r.get('uid')==aid),None)
            if row is None: raise HTTPException(404,'账号不存在')
            return {'remaining':row.get('balance',0)/1000,'daily_token_balance':row.get('daily_token_balance'),'expires_at':row.get('expires_at')}
        if provider in {'qoder','qoder-cn','qoder_cn'}:
            account=self.qoder.get(aid)
            value=await self.qoder.transport.request(account,'/api/v2/quota/usage')
            return metadata({'provider':provider,'account_id':aid,'quota':value,'plan':account.plan},
                (account.access_token,account.refresh_token,account.personal_token))
        if provider in {'workbuddy','workbuddy_intl'}:
            await self.workbuddy.account(aid)
            result=await self.workbuddy.hub('GET','/accounts/credits/detail?uid='+quote(aid,safe='')+'&refresh=1')
            return metadata(result,(self.workbuddy.api_key,self.workbuddy.admin_key,self.workbuddy.panel_token))
        if provider=='codebuddy':
            item,_=self.pool.snapshot(aid)
            manager=self.store.manager_for(aid,item)
            def lookup():
                with self.pool.client_factory() as client:
                    return self.pool.credits(client,manager.get_headers(),include_details=True)
            return metadata(await asyncio.to_thread(lookup))
        raise HTTPException(400,'此平台的余额明细尚未由上游提供，请查询账号余额')

    async def run_all_benefits(self):
        rows=[{**row,'provider':'codebuddy'} for row in self.store.account_rows()]
        rows += await self.qoder.accounts()
        results=[]
        for row in rows:
            if row.get('enabled') is False: continue
            actions=['benefits','streak','travel'] if row['provider']=='codebuddy' else ['benefits']
            for action in actions:
                try: result=await self.run(row['provider'],row['id'],action)
                except Exception: result={'ok':False,'message':'福利任务失败，请在福利页面查看账号状态'}
                results.append({'id':row['id'],'provider':row['provider'],'action':action,**result})
        return {'providers':[{'provider':'benefits','results':results}],
                'summary':{'total':len(results),'succeeded':sum(bool(r.get('ok')) for r in results),'failed':sum(not r.get('ok') for r in results)}}

    async def settings(self,body=None):
        if body is not None:
            if not isinstance(body,dict) or set(body)-SETTINGS: raise HTTPException(400,'设置包含不支持的字段')
            for key in ('reserve_credits','daily_token_limit','daily_credit_limit','model_daily_token_limit'):
                if key in body and (type(body[key]) is not int or not 0<=body[key]<=10**12):
                    raise HTTPException(400,'账号限额应为非负整数')
            for key in ('local_web_tools','pricing_enabled','daily_chat_web','remaining_priority_enabled'):
                if key in body and type(body[key]) is not bool: raise HTTPException(400,'开关应为布尔值')
            await self.workbuddy.hub('POST','/settings/save',body=body)
        value=await self.workbuddy.hub('GET','/settings')
        return {key:value[key] for key in SETTINGS if key in value}

    def register(self,app,body_json):
        @app.get('/admin/api/unified/tasks/history')
        async def history(req:Request):
            self.store.require_admin(req)
            return {'items':list(reversed(self.config.get('activity_history',[])))}
        @app.get('/admin/api/unified/tasks/{provider}/{aid}')
        async def view(provider:str,aid:str,req:Request):
            self.store.require_admin(req); return await self.view(provider,aid)
        @app.post('/admin/api/unified/tasks/{provider}/{aid}/{action}')
        async def run(provider:str,aid:str,action:str,req:Request):
            self.store.require_admin(req); return await self.run(provider,aid,action)
        @app.get('/admin/api/unified/accounts/{provider}/{aid}/details')
        async def detail(provider:str,aid:str,req:Request):
            self.store.require_admin(req); return await self.quota(provider,aid)
        @app.api_route('/admin/api/unified/provider-settings/workbuddy',methods=['GET','PATCH'])
        async def settings(req:Request):
            self.store.require_admin(req)
            return await self.settings(await body_json(req) if req.method=='PATCH' else None)
        @app.get('/admin/api/unified/credits/grants')
        async def grants(req:Request):
            self.store.require_admin(req)
            return metadata(await self.workbuddy.hub('GET','/accounts/credits/grants'),
                (self.workbuddy.api_key,self.workbuddy.admin_key,self.workbuddy.panel_token))
