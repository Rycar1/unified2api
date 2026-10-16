import json
import tempfile
import unittest
from unittest.mock import AsyncMock
import httpx
from fastapi.responses import JSONResponse
from admin.server import create_app as create_buddy
from unified.server import create_app


class Native:
    async def request(self,method,path,body=b''): return 200,{'data':[],'accounts':[]}


class ReliabilityAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        buddy=create_buddy(root=self.temp.name+'/management',auth_dir=self.temp.name+'/auth',initial_key='synthetic-client',admin_key='synthetic-admin-long-key',secure_cookie=False)
        self.app=create_app(native=Native(),buddy=buddy)
        wb=self.app.state.workbuddy_login
        wb.models=AsyncMock(return_value=[{'id':'workbuddy/glm-5.3-flash','owned_by':'workbuddy'}])
        wb.accounts=AsyncMock(return_value=[])
        self.sent=[]
        async def relay(path,body,scope,receive,send):
            self.sent.append(json.loads(body)['model'])
            return await JSONResponse({'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]})(scope,receive,send)
        wb.relay=relay
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        login=await self.client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.csrf={'X-CSRF-Token':login.json()['csrf']}
        self.auth={'Authorization':'Bearer synthetic-client'}
        self.root='/admin/api/unified'

    async def asyncTearDown(self):
        if self.app.state.probes.task: await self.app.state.probes.task
        await self.client.aclose();self.temp.cleanup()

    async def test_alias_validation_dispatch_catalog_and_permission_checks(self):
        body={'id':'alias/fast','target':'workbuddy/glm-5.3-flash'}
        self.assertEqual((await self.client.post(self.root+'/model-aliases',json=body)).status_code,403)
        self.assertEqual((await self.client.post(self.root+'/model-aliases',json=body,headers=self.csrf)).status_code,200)
        models=(await self.client.get('/v1/models',headers=self.auth)).json()['data']
        self.assertEqual(next(m for m in models if m['id']=='alias/fast')['alias_target'],body['target'])
        bad=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'workbuddy/glm-53-flash','messages':[]})
        self.assertEqual(bad.json()['error']['code'],'model_not_found')
        self.assertIn(body['target'],bad.json()['error']['suggestions'])
        good=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'alias/fast','messages':[]})
        self.assertEqual(good.status_code,200);self.assertEqual(self.sent,['glm-5.3-flash'])
        kid=next(iter(self.app.state.buddy.state.store.data['keys']))
        await self.client.patch(self.root+'/key-limits/'+kid,headers=self.csrf,json={'allowed_models':['alias/fast']})
        self.assertEqual((await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'alias/fast','messages':[]})).status_code,403)

    async def test_restore_deleted_service_keeps_secrets_private_and_other_settings(self):
        body={'id':'example','name':'Example','base_url':'https://example.test/v1','key':'synthetic-upstream-secret','models':['m']}
        self.assertEqual((await self.client.post(self.root+'/connections',headers=self.csrf,json=body)).status_code,200)
        await self.client.patch(self.root+'/network',headers=self.csrf,json={'idle_seconds':99})
        await self.client.delete(self.root+'/connections/example',headers=self.csrf)
        history=(await self.client.get(self.root+'/config-history')).json()
        row=next(r for r in history['items'] if r['kind']=='connection')
        detail=await self.client.get(self.root+'/config-history/'+str(row['id']))
        self.assertNotIn('synthetic-upstream-secret',detail.text)
        url=self.root+'/config-history/'+str(row['id'])+'/restore'
        self.assertEqual((await self.client.post(url,json={'fingerprint':detail.json()['fingerprint']})).status_code,403)
        result=await self.client.post(url,headers=self.csrf,json={'fingerprint':detail.json()['fingerprint']})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.app.state.connections.get('example')['key'],body['key'])
        self.assertEqual((await self.client.get(self.root+'/network')).json()['settings']['idle_seconds'],99)
        changed=await self.client.patch(self.root+'/connections/example',headers=self.csrf,json={'name':'Concurrent edit'})
        self.assertEqual(changed.status_code,200)
        self.assertEqual((await self.client.post(url,headers=self.csrf,json={'fingerprint':detail.json()['fingerprint']})).status_code,409)

    async def test_provider_error_records_only_safe_code_and_category(self):
        await self.client.post(self.root+'/connections',headers=self.csrf,json={'id':'example','name':'Example','base_url':'https://example.test/v1','key':'synthetic-upstream-secret','models':['m']})
        self.app.state.connections.transport=httpx.MockTransport(lambda r:httpx.Response(400,json={'code':11102,'msg':'synthetic-private-prompt-echo'}))
        failed=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'example/m','messages':[]})
        self.assertEqual(failed.status_code,400)
        detail=(await self.client.get(self.root+'/diagnostics/'+failed.headers['x-request-id'])).json()
        self.assertEqual(detail['diagnosis']['category'],'model_not_found')
        self.assertEqual(detail['diagnosis']['upstream_code'],'11102')
        self.assertNotIn('synthetic-private-prompt-echo',json.dumps(detail))

    async def test_probe_and_credit_settings_require_admin_csrf(self):
        self.assertEqual((await self.client.patch(self.root+'/probe-plans',json={'enabled':True})).status_code,403)
        value=await self.client.patch(self.root+'/probe-plans',headers=self.csrf,json={'models':['workbuddy/glm-5.3-flash'],'checks':['chat'],'daily_requests':1})
        self.assertEqual(value.status_code,200,value.text)
        self.assertEqual((await self.client.post(self.root+'/probe-plans/run',headers=self.csrf,json={})).status_code,200)
        await self.app.state.probes.task
        result=(await self.client.get(self.root+'/probe-plans')).json()
        self.assertEqual(result['usage']['requests'],1);self.assertTrue(result['history'][0]['ok'])
        result=await self.client.patch(self.root+'/credit-policy',headers=self.csrf,json={'window_days':3})
        self.assertEqual(result.json()['settings']['window_days'],3)
        self.assertEqual((await self.client.get(self.root+'/credit-policy/expiring')).json()['items'],[])

    async def test_alias_to_route_cannot_become_a_recursive_route_target(self):
        route={'id':'stable','targets':['workbuddy/glm-5.3-flash']}
        self.assertEqual((await self.client.post(self.root+'/routes',headers=self.csrf,json=route)).status_code,200)
        self.assertEqual((await self.client.post(self.root+'/model-aliases',headers=self.csrf,json={'id':'alias/stable','target':'route/stable'})).status_code,200)
        denied=await self.client.patch(self.root+'/routes/stable',headers=self.csrf,json={'targets':['alias/stable']})
        self.assertEqual(denied.status_code,400)
        result=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'alias/stable','messages':[]})
        self.assertEqual(result.status_code,200,result.text)
        config=self.app.state.api.routes.config
        rows=config.get('routes',[]);rows[0]['targets']=['alias/stable'];config.set('routes',rows)
        cycle=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'alias/stable','messages':[]})
        self.assertEqual(cycle.status_code,400)
        self.assertEqual(cycle.json()['error']['code'],'route_cycle')
