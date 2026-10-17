import copy
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit, urlencode
from unittest.mock import AsyncMock, patch
import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app


class Native:
    async def request(self, method, path, body=b''): return 200, {'data':[], 'accounts':[]}


class AntigravityAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'ANTIGRAVITY_CLIENT_ID':'synthetic-client-id','ANTIGRAVITY_CLIENT_SECRET':'synthetic-client-secret'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        buddy = create_buddy(root=self.temp.name+'/management', auth_dir=self.temp.name+'/auth',
            initial_key='synthetic-client', admin_key='synthetic-admin-long-key', secure_cookie=False)
        self.app = create_app(native=Native(), buddy=buddy)
        self.app.state.workbuddy_login.models = AsyncMock(return_value=[])
        self.app.state.workbuddy_login.accounts = AsyncMock(return_value=[])
        self.pool = self.app.state.antigravity
        self.streams, self.token_calls = [], 0
        self.truncated = False
        self.fail_first = False
        self.tool_reply = False

        async def respond(req):
            if req.url.host == 'oauth2.googleapis.com':
                self.token_calls += 1
                return httpx.Response(200,json={'access_token':'synthetic-access-'+str(self.token_calls), 'refresh_token':'1//synthetic-refresh', 'expires_in':3600})
            if req.url.path.endswith(':loadCodeAssist'):
                return httpx.Response(200,json={'cloudaicompanionProject':'synthetic-project'})
            if req.url.path.endswith(':fetchAvailableModels'):
                return httpx.Response(200,json={'models':{'gemini-test':{'supportsThinking':True}}})
            if req.url.path.endswith(':retrieveUserQuotaSummary'):
                return httpx.Response(200,json={'groups':[{'displayName':'Gemini','buckets':[{'remainingFraction':0.6,'resetTime':'2026-10-17T00:00:00Z'}]}]})
            if req.url.path.endswith(':streamGenerateContent'):
                self.streams.append(req)
                if self.fail_first and len(self.streams)==1:
                    return httpx.Response(429,json={'error':{'message':'RESOURCE_EXHAUSTED'}})
                part = {'functionCall':{'name':'lookup','args':{'key':'value'},'id':'call-synthetic'},'thoughtSignature':'synthetic-signature'} if self.tool_reply else {'text':'Synthetic response'}
                value = {'response':{'candidates':[{'content':{'parts':[part]}}], 'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':3,'totalTokenCount':13}}}
                if not self.truncated: value['response']['candidates'][0]['finishReason']='STOP'
                return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text='data: '+json.dumps(value)+'\n\n')
            return httpx.Response(200,json={'email':'synthetic@example.test'})

        self.pool.transport = httpx.MockTransport(respond)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost:8080')
        login = await self.client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.csrf = {'X-CSRF-Token':login.json()['csrf']}
        self.auth = {'Authorization':'Bearer synthetic-client'}
        self.base = '/admin/api/unified/antigravity'

    async def asyncTearDown(self):
        await self.client.aclose(); await self.pool.close(); self.temp.cleanup()
        self.env.stop()

    async def add(self):
        reply = await self.client.post(self.base+'/accounts',headers=self.csrf,json={'credential':'1//synthetic-refresh'})
        self.assertEqual(reply.status_code,200,reply.text)
        return reply.json()

    async def test_admin_csrf_and_account_crud_never_return_credentials(self):
        denied = await self.client.post(self.base+'/accounts',json={'credential':'1//synthetic-refresh'})
        self.assertEqual(denied.status_code,403)
        row = await self.add()
        changed = await self.client.patch(self.base+'/accounts/'+row['id'],headers=self.csrf,json={'name':'Renamed','enabled':False})
        self.assertEqual(changed.json()['name'],'Renamed')
        self.assertEqual(changed.json()['status'],'paused')
        public = await self.client.get(self.base+'/accounts')
        self.assertNotIn('synthetic-access-',public.text)
        self.assertNotIn('1//synthetic-refresh',public.text)
        self.assertEqual((await self.client.delete(self.base+'/accounts/'+row['id'],headers=self.csrf)).status_code,200)

    async def test_chat_responses_messages_and_catalog_routing(self):
        await self.add()
        models = await self.client.get('/v1/models',headers=self.auth)
        row = next(r for r in models.json()['data'] if r['id']=='antigravity/gemini-test')
        self.assertEqual(row['capabilities']['protocols']['responses'],'converted')
        for path,body in [('/v1/chat/completions',{'messages':[{'role':'user','content':'Synthetic'}]}),
                          ('/v1/responses',{'input':'Synthetic'}),
                          ('/v1/messages',{'messages':[{'role':'user','content':'Synthetic'}],'max_tokens':32})]:
            reply = await self.client.post(path,headers=self.auth,json={**body,'model':'antigravity/gemini-test','stream':False})
            self.assertEqual(reply.status_code,200,reply.text)
            self.assertIn('Synthetic response',reply.text)
        route={'id':'synthetic-ag','targets':['antigravity/gemini-test'],'strategy':'priority','retries':0}
        self.assertEqual((await self.client.post('/admin/api/unified/routes',headers=self.csrf,json=route)).status_code,200)
        response=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'route/synthetic-ag','messages':[{'role':'user','content':'Synthetic'}]})
        self.assertEqual(response.status_code,200,response.text)
        compact=await self.client.post('/v1/responses/compact',headers=self.auth,json={'model':'antigravity/gemini-test','input':'Synthetic'})
        self.assertEqual(compact.status_code,400)

    async def test_quota_details_and_scoped_maintenance_skip_unsupported_checkin(self):
        row=await self.add()
        detail=await self.client.get('/admin/api/unified/accounts/antigravity/'+row['id']+'/details')
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual(detail.json()['quota'][0]['remaining'],60)
        from unified.maintenance import ScopedMaintenance
        runner=ScopedMaintenance(None,None,None,None,None,self.app.state.connections,self.app.state.task_center,self.pool)
        outcome=await runner.run('status',providers=['antigravity'])
        self.assertEqual(outcome['summary']['succeeded'],1)
        skipped=await runner.run('checkin',providers=['antigravity'])
        self.assertIn('跳过',skipped['providers'][0]['results'][0]['message'])

    async def test_responses_continuation_rejects_other_gateway_keys(self):
        await self.add()
        reply=await self.client.post('/v1/responses',headers=self.auth,json={'model':'antigravity/gemini-test','input':'Synthetic','stream':False})
        rid=reply.json()['id']
        second=await self.client.post('/v1/responses',headers=self.auth,json={'model':'antigravity/gemini-test','previous_response_id':rid,'input':'Synthetic again','stream':False})
        self.assertEqual(second.status_code,200,second.text)
        self.assertGreaterEqual(len(json.loads(self.streams[-1].content)['request']['contents']),3)
        self.pool.store.add_key('Other synthetic key','synthetic-other-client')
        denied=await self.client.post('/v1/responses',headers={'Authorization':'Bearer synthetic-other-client'},json={'model':'antigravity/gemini-test','previous_response_id':rid,'input':'Synthetic','stream':False})
        self.assertEqual(denied.status_code,400)

    async def test_one_empty_model_group_does_not_disable_other_gemini_groups(self):
        await self.add()
        self.pool.rows[0]['remaining']=0
        self.app.state.workbuddy_login.public_accounts=AsyncMock(return_value=[])
        from unified.routing import inventory
        state=await inventory(self.app.state.config_changes.api)
        self.assertTrue(state['antigravity']['available'])

    async def test_loopback_callback_works_without_admin_cookie_but_requires_state(self):
        flow=(await self.client.post(self.base+'/login',headers=self.csrf,json={})).json()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost:8080') as browser:
            wrong=await browser.get(self.base+'/callback',params={'state':'wrong','code':'synthetic-code'})
            self.assertEqual(wrong.status_code,400)
            result=await browser.get(self.base+'/callback',params={'state':flow['id'],'code':'synthetic-code'})
            self.assertEqual(result.status_code,200,result.text)
            self.assertNotIn('synthetic-code',result.text)
        self.assertEqual((await self.client.get(self.base+'/login/'+flow['id'])).json()['state'],'success')

    async def test_multi_account_quota_failover_and_signed_tool_affinity(self):
        first=await self.add()
        other=copy.deepcopy(self.pool.rows[0]); other.update(id='synthetic-second',access_token='synthetic-second-access')
        self.pool.rows.append(other); self.pool.save()
        self.fail_first=True; self.tool_reply=True
        request={'model':'antigravity/gemini-test','messages':[{'role':'user','content':'Synthetic'}]}
        reply=await self.client.post('/v1/chat/completions',headers=self.auth,json=request)
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(len(self.streams),2)
        self.assertNotEqual(self.streams[0].headers['authorization'],self.streams[1].headers['authorization'])
        assistant=reply.json()['choices'][0]['message']
        self.assertEqual(assistant['tool_calls'][0]['extra_content']['google']['thought_signature'],'synthetic-signature')
        self.pool.rows[0]['model_cooldowns']={}; self.pool.cursor=0; self.tool_reply=False
        request['messages'] += [assistant,{'role':'tool','tool_call_id':'call-synthetic','content':'{"answer":42}'}]
        reply=await self.client.post('/v1/chat/completions',headers=self.auth,json=request)
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual(self.streams[-1].headers['authorization'],'Bearer synthetic-second-access')

    async def test_truncated_stream_has_error_and_no_success_finish(self):
        await self.add(); self.truncated=True
        reply=await self.client.post('/v1/chat/completions',headers=self.auth,json={'model':'antigravity/gemini-test','stream':True,'messages':[{'role':'user','content':'Synthetic'}]})
        self.assertIn('"error"',reply.text)
        self.assertNotIn('"finish_reason":"stop"',reply.text)

    async def test_oauth_state_owner_manual_callback_and_replay(self):
        flow=(await self.client.post(self.base+'/login',headers=self.csrf,json={})).json()
        query=parse_qs(urlsplit(flow['url']).query)
        self.assertEqual(query['state'],[flow['id']])
        foreign=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost:8080')
        await foreign.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.assertEqual((await foreign.get(self.base+'/login/'+flow['id'])).status_code,404)
        await foreign.aclose()
        redirect=query['redirect_uri'][0]
        reply=await self.client.post(self.base+'/login/'+flow['id']+'/callback',headers=self.csrf,json={'url':redirect+'?'+urlencode({'state':'wrong','code':'synthetic-code'})})
        self.assertEqual(reply.status_code,400)
        correct=redirect+'?'+urlencode({'state':flow['id'],'code':'synthetic-code'})
        reply=await self.client.post(self.base+'/login/'+flow['id']+'/callback',headers=self.csrf,json={'url':correct})
        self.assertEqual(reply.status_code,200,reply.text)
        self.assertEqual((await self.client.get(self.base+'/login/'+flow['id'])).json()['state'],'success')
        replay=await self.client.post(self.base+'/login/'+flow['id']+'/callback',headers=self.csrf,json={'url':correct})
        self.assertEqual(replay.status_code,409)
