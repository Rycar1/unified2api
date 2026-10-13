import asyncio
import json
import tempfile
import unittest
import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app


class Native:
    async def request(self,method,path,body=b''):
        return 200,{'data':[{'id':'synthetic'}],'accounts':[]}


class ControlAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        buddy = create_buddy(root=self.temp.name+'/management',auth_dir=self.temp.name+'/auth',
                            initial_key='synthetic-public-key',admin_key='synthetic-admin-key-long-enough',secure_cookie=False)
        self.app = create_app(native=Native(),buddy=buddy)
        self.app.state.connections.save({'id':'alpha','name':'Alpha','base_url':'https://alpha.test/v1',
                                         'key':'synthetic-upstream-secret','models':['one','two']})
        self.app.state.connections.save({'id':'beta','name':'Beta','base_url':'https://beta.test/v1',
                                         'key':'synthetic-upstream-secret','models':['one']})
        self.mode = 'normal'
        self.gate,self.entered = asyncio.Event(),asyncio.Event()
        self.calls = []
        async def upstream(req):
            self.calls.append(req.url.host)
            if self.mode=='blocked':
                self.entered.set(); await self.gate.wait()
            if self.mode in {'failover','failover-unknown','failed-usage'} and req.url.host=='alpha.test':
                return httpx.Response(503,json={'error':{'message':'PRIVATE-SYNTHETIC-PROMPT'},'usage':{'prompt_tokens':1,'completion_tokens':0}})
            if self.mode=='stream':
                return httpx.Response(200,headers={'content-type':'text/event-stream'},content=b'data: {"choices":[{"delta":{"content":"PRIVATE-SYNTHETIC-REPLY"},"finish_reason":null}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":5}}\n\ndata: [DONE]\n\n')
            if req.url.path.endswith('/compact'):
                return httpx.Response(200,json={'output':[{'type':'compaction','encrypted_content':'opaque-synthetic'}],'usage':{'input_tokens':1,'output_tokens':1}})
            if self.mode=='failover-unknown':
                return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'synthetic'},'finish_reason':'stop'}]})
            return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'PRIVATE-SYNTHETIC-REPLY'},'finish_reason':'stop'}],
                                          'usage':{'prompt_tokens':3,'completion_tokens':5}})
        self.app.state.connections.transport = httpx.MockTransport(upstream)
        async def hub(req):
            return httpx.Response(200,json={'token':'synthetic-panel','accounts':[],'data':[]})
        self.app.state.workbuddy_login.client_factory = lambda:httpx.AsyncClient(transport=httpx.MockTransport(hub))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        result = await self.client.post('/admin/api/login',json={'key':'synthetic-admin-key-long-enough'})
        self.csrf = {'X-CSRF-Token':result.json()['csrf']}
        self.kid = next(iter(buddy.state.store.data['keys']))
        self.api_headers = {'Authorization':'Bearer synthetic-public-key'}
        self.body = {'model':'alpha/one','max_tokens':16,'messages':[{'role':'user','content':'PRIVATE-SYNTHETIC-PROMPT'}]}

    async def asyncTearDown(self):
        await self.client.aclose(); self.temp.cleanup()

    async def test_limit_endpoints_require_admin_csrf_and_enforce_model_policy(self):
        path = '/admin/api/unified/key-limits/'+self.kid
        self.assertEqual((await self.client.patch(path,json={})).status_code,403)
        response = await self.client.patch(path,headers=self.csrf,json={'allowed_models':['alpha/one'],'daily_tokens':10000,'max_concurrency':1})
        self.assertEqual(response.status_code,200,response.text)
        models = (await self.client.get('/v1/models',headers=self.api_headers)).json()['data']
        self.assertEqual([row['id'] for row in models],['alpha/one'])
        denied = await self.client.post('/v1/chat/completions',headers=self.api_headers,json={**self.body,'model':'alpha/two'})
        self.assertEqual(denied.status_code,403)
        accepted = await self.client.post('/v1/chat/completions',headers=self.api_headers,json=self.body)
        self.assertEqual(accepted.status_code,200)
        self.assertEqual(self.app.state.controls.usage(self.kid)['day_tokens'],8)
        await self.client.patch(path,headers=self.csrf,json={'enabled':False})
        self.assertEqual((await self.client.get('/v1/models',headers=self.api_headers)).status_code,403)

    async def test_concurrency_is_released_after_request_completion(self):
        await self.client.patch('/admin/api/unified/key-limits/'+self.kid,headers=self.csrf,json={'max_concurrency':1})
        self.mode = 'blocked'
        first = asyncio.create_task(self.client.post('/v1/chat/completions',headers=self.api_headers,json=self.body))
        await asyncio.wait_for(self.entered.wait(),2)
        second = await self.client.post('/v1/chat/completions',headers=self.api_headers,json=self.body)
        self.assertEqual(second.status_code,429)
        self.gate.set(); self.assertEqual((await first).status_code,200)
        self.assertEqual(self.app.state.controls.usage(self.kid)['active'],0)

    async def test_smart_failover_produces_diagnostics_without_storing_content(self):
        route = await self.client.post('/admin/api/unified/routes',headers=self.csrf,
            json={'id':'smart','targets':['alpha/one','beta/one'],'strategy':'smart','retries':1})
        self.assertEqual(route.status_code,200,route.text)
        self.mode = 'failover'
        response = await self.client.post('/v1/chat/completions',headers=self.api_headers,json={**self.body,'model':'route/smart'})
        self.assertEqual(response.status_code,200,response.text)
        request_id = response.headers['x-request-id']
        detail = await self.client.get('/admin/api/unified/diagnostics/'+request_id)
        self.assertEqual(detail.status_code,200)
        data = detail.json()
        self.assertEqual([row['status'] for row in data['attempts']],[503,200])
        self.assertEqual(data['resolved_model'],'beta/one')
        self.assertNotIn('PRIVATE-SYNTHETIC',json.dumps(data))
        self.assertEqual(self.app.state.controls.usage(self.kid)['day_tokens'],9)
        found = await self.client.get('/admin/api/unified/logs',params={'model':request_id})
        self.assertEqual(found.json()['total'],1)
        self.assertEqual(self.app.state.api.routes.active['beta/one'],0)

    async def test_stream_finish_and_compact_share_key_limits(self):
        self.mode = 'stream'
        response = await self.client.post('/v1/chat/completions',headers=self.api_headers,json={**self.body,'stream':True})
        self.assertEqual(response.status_code,200)
        detail = (await self.client.get('/admin/api/unified/diagnostics/'+response.headers['x-request-id'])).json()
        self.assertTrue(detail['ok']); self.assertIsNotNone(detail['ttft_ms'])
        self.assertEqual(detail['finish_reason'],'stop')
        self.mode = 'normal'
        response = await self.client.post('/v1/responses/compact',headers=self.api_headers,json={'model':'alpha/one','input':'synthetic'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.app.state.controls.usage(self.kid)['day_tokens'],10)

    async def test_capability_overrides_and_unavailable_release_ui(self):
        path = '/admin/api/unified/model-capabilities/alpha/one'
        response = await self.client.patch(path,headers=self.csrf,json={'tools':True,'context_window':64000})
        self.assertEqual(response.status_code,200,response.text)
        models = (await self.client.get('/v1/models',headers=self.api_headers)).json()['data']
        self.assertTrue(next(row for row in models if row['id']=='alpha/one')['capabilities']['tools'])
        self.assertEqual((await self.client.get('/admin/api/unified/releases')).status_code,200)
        self.assertEqual((await self.client.post('/admin/api/unified/releases/upgrade',json={})).status_code,403)

    async def test_final_failed_attempt_reported_usage_is_billed(self):
        self.mode = 'failed-usage'
        response = await self.client.post('/v1/chat/completions',headers=self.api_headers,json=self.body)
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.app.state.controls.usage(self.kid)['day_tokens'],1)
        detail = (await self.client.get('/admin/api/unified/diagnostics/'+response.headers['x-request-id'])).json()
        self.assertEqual(detail['prompt_tokens'],1)

    async def test_unknown_final_usage_is_estimated_in_addition_to_reported_retry(self):
        await self.client.patch('/admin/api/unified/key-limits/'+self.kid,headers=self.csrf,json={'daily_tokens':10000})
        await self.client.post('/admin/api/unified/routes',headers=self.csrf,
            json={'id':'unknown','targets':['alpha/one','beta/one'],'strategy':'priority','retries':1})
        self.mode = 'failover-unknown'
        response = await self.client.post('/v1/chat/completions',headers=self.api_headers,json={**self.body,'model':'route/unknown'})
        self.assertEqual(response.status_code,200)
        estimate = self.app.state.controls.estimate(self.body)
        usage = self.app.state.controls.usage(self.kid)
        self.assertEqual(usage['day_tokens'],estimate+1)
        self.assertEqual(usage['estimated_tokens'],estimate)

    async def test_cancelled_request_releases_key_concurrency(self):
        await self.client.patch('/admin/api/unified/key-limits/'+self.kid,headers=self.csrf,json={'max_concurrency':1})
        self.mode = 'blocked'
        task = asyncio.create_task(self.client.post('/v1/chat/completions',headers=self.api_headers,json=self.body))
        await asyncio.wait_for(self.entered.wait(),2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.app.state.controls.usage(self.kid)['active'],0)
