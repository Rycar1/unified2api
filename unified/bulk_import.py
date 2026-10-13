"""Preview imports and apply each row through existing authenticated controllers."""
import json
import importlib
from pathlib import Path
import sys
import httpx
from fastapi import HTTPException,Request

PROVIDERS={'trae','codebuddy','monkeycode','qoder','qoder-cn','workbuddy_intl'}


def import_rows(body):
    provider=body.get('provider')
    if provider not in PROVIDERS: raise HTTPException(400,'请选择支持的平台')
    values=body.get('credentials',body.get('credential'))
    if isinstance(values,str):
        try: values=json.loads(values)
        except ValueError:
            if provider=='qoder' and values.startswith('pt-'): values=[values]
            elif provider=='monkeycode' and values.strip(): values=[{'cookie':values}]
            else: raise HTTPException(400,'请输入有效的凭据 JSON 数组') from None
    if not isinstance(values,list): values=[values]
    if not 1<=len(values)<=100: raise HTTPException(400,'一次可导入 1–100 个账号')
    result=[]
    for index,value in enumerate(values):
        valid=isinstance(value,dict) or provider.startswith('qoder') and isinstance(value,str) and value.startswith('pt-')
        record=value.get('user',value) if isinstance(value,dict) else {}
        if isinstance(record,dict): record=record.get('auth_user_info_raw',record)
        uid=record.get('uid') or record.get('userId') or record.get('id') if isinstance(record,dict) else None
        if provider in {'codebuddy','trae'} and isinstance(value,dict): uid=(value.get('account') or {}).get('uid') or uid
        name=record.get('nickname') or record.get('name') if isinstance(record,dict) else None
        result.append({'index':index,'valid':valid,'uid':str(uid) if uid else None,
                       'name':str(name or body.get('name') or '账号 '+str(index+1))[:60],
                       'region':'cn' if provider=='qoder-cn' else body.get('region','global'),'value':value})
    return provider,result


class BulkImport:
    def __init__(self,app,store,pool,qoder,workbuddy,api):
        self.app,self.store,self.pool,self.qoder,self.workbuddy,self.api=app,store,pool,qoder,workbuddy,api

    async def execute(self,body,req):
        if not isinstance(body,dict) or any(key in body and type(body[key]) is not bool for key in ('preview','overwrite')):
            raise HTTPException(400,'预览和覆盖选项必须为布尔值')
        provider,rows=import_rows(body)
        for row in rows:
            try:
                if not row['valid']: continue
                if provider=='codebuddy': self.store.validate_credential(row['value'])
                elif provider=='workbuddy_intl':
                    source=str(Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub')
                    if source not in sys.path: sys.path.insert(0,source)
                    normalized=importlib.import_module('wb_accounts').normalise_import_row(row['value'],realm='intl')
                    row['uid']=str(normalized['uid'])
                    row['name']=normalized.get('nickname') or row['name']
            except (HTTPException,ValueError,TypeError,KeyError,AttributeError): row['valid']=False
        existing={}
        if provider=='codebuddy': existing={str(row.get('uid')):row['id'] for row in self.store.account_rows()}
        elif provider.startswith('qoder'):
            region='cn' if provider=='qoder-cn' else body.get('region','global')
            existing={row['uid']:row['id'] for row in await self.qoder.accounts() if row['region']==region}
        elif provider=='workbuddy_intl': existing={row['uid']:row['uid'] for row in await self.workbuddy.accounts()}
        elif provider in {'trae','monkeycode'}:
            code,value=await self.api.native.request('GET','/admin/api/accounts' if provider=='trae' else '/monkey/admin/accounts')
            if code==200: existing={str(row['uid']):row['uid'] for row in value.get('accounts',[])}
        for row in rows: row['exists']=bool(row['uid'] and row['uid'] in existing)
        public=lambda row:{key:row.get(key) for key in ('index','valid','uid','name','region','exists')}
        if body.get('preview',True): return {'provider':provider,'rows':[public(row) for row in rows],'total':len(rows)}
        overwrite=body.get('overwrite') is True
        headers={key:value for key,value in req.headers.items() if key.lower() in {'cookie','x-csrf-token'}}
        results=[]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://internal') as client:
            for row in rows:
                result=public(row)
                if not row['valid']: result.update(ok=False,state='invalid',message='凭据格式无效')
                elif row['uid'] in existing and not overwrite: result.update(ok=True,state='skipped',message='已有账号，已跳过')
                else:
                    value=row['value']
                    try:
                        if provider=='workbuddy_intl':
                            response=await self.workbuddy.hub('POST','/accounts/import',body={'data':[value],
                                'overwrite':overwrite,'realm':'intl'})
                            report=response.get('result') or {}
                            if report.get('invalid'): raise ValueError('invalid credential')
                            state='updated' if report.get('updated') else 'skipped' if report.get('skipped') else 'imported'
                            result.update(ok=True,state=state,message='已有账号，已跳过' if state=='skipped' else '账号已导入')
                        elif provider=='codebuddy' and row['uid'] in existing:
                            item=self.store.data['accounts'][existing[row['uid']]]
                            self.store.validate_credential(value)
                            from admin.server import write_json
                            with self.store.lock:
                                write_json(self.store.file_for(item),value)
                                self.store.managers.pop(existing[row['uid']],None)
                                self.store.sync_active()
                            result.update(ok=True,state='updated',message='已有凭据已更新')
                        else:
                            target='qoder' if provider.startswith('qoder') else provider
                            path='/admin/api/accounts' if provider=='codebuddy' else '/admin/api/unified/'+target+'/accounts'
                            payload={'name':row['name'],'credential':value,'overwrite':overwrite,'region':row['region']}
                            if provider=='monkeycode' and isinstance(value,dict) and value.get('cookie'): payload=value
                            response=await client.post(path,headers=headers,json=payload)
                            if response.status_code>=300: raise HTTPException(response.status_code,'凭据校验或授权交换失败')
                            result.update(ok=True,state='imported',message='账号已导入')
                            imported=response.json()
                            if row['uid']: existing[row['uid']]=imported.get('id') or imported.get('uid') or existing.get(row['uid'],row['uid'])
                        if row['uid'] and result.get('ok'): existing[row['uid']]=existing.get(row['uid'],row['uid'])
                    except (HTTPException,ValueError,TypeError,OSError):
                        result.update(ok=False,state='failed',message='凭据校验或授权交换失败，请检查这条账号')
                results.append(result)
        return {'provider':provider,'results':results,'imported':sum(row['state'] in {'imported','updated'} for row in results),
                'skipped':sum(row['state']=='skipped' for row in results),'failed':sum(not row['ok'] for row in results)}

    def register(self,app,body_json):
        @app.post('/admin/api/unified/accounts/import')
        async def import_accounts(req:Request):
            self.store.require_admin(req)
            return await self.execute(await body_json(req),req)
