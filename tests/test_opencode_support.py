import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app
from unified.custom import Connections


class Native:
    async def request(self, method, path, body=b''):return 200,{'data':[],'accounts':[]}


class OpenCodeSupportAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        buddy=create_buddy(root=self.temp.name+'/management',auth_dir=self.temp.name+'/auth',
            initial_key='synthetic-public',admin_key='synthetic-admin-long-key',secure_cookie=False)
        self.app=create_app(native=Native(),buddy=buddy)
        self.app.state.workbuddy_login.models=AsyncMock(return_value=[])
        self.app.state.workbuddy_login.public_accounts=AsyncMock(return_value=[])
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        login=await self.client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.csrf={'X-CSRF-Token':login.json()['csrf']}
        self.auth={'Authorization':'Bearer synthetic-public'}
        self.calls=[]
        self.app.state.connections.transport=httpx.MockTransport(self.upstream)
        self.body={'id':'oc','name':'Synthetic OpenCode','base_url':'https://opencode.example/v1',
            'key':'synthetic-upstream-key','models':['model'],'opencode_support':True}

    async def asyncTearDown(self):
        await self.client.aclose();self.temp.cleanup()

    def upstream(self,request):
        self.calls.append(request)
        if request.url.path.endswith('/responses'):
            return httpx.Response(200,json={'id':'resp-synthetic-'+str(len(self.calls)),'object':'response',
                'status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Synthetic reply'}]}]})
        return httpx.Response(200,json={'choices':[{'message':{'role':'assistant','content':'Synthetic reply'},'finish_reason':'stop'}]})

    async def add(self,**extra):
        response=await self.client.post('/admin/api/unified/connections',json={**self.body,**extra},headers=self.csrf)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def chat(self,messages=None,headers=None):
        return await self.client.post('/v1/chat/completions',headers={**self.auth,**(headers or {})},json={
            'model':'oc/model','messages':messages or [{'role':'user','content':'Synthetic first prompt'}]})

    async def test_opt_in_is_strict_persistent_editable_and_defaults_off(self):
        added=await self.add(opencode_support=False)
        self.assertTrue(added['ok'])
        self.assertFalse(self.app.state.connections.get('oc')['opencode_support'])
        for bad in ['true',1,None,{}]:
            response=await self.client.patch('/admin/api/unified/connections/oc',json={'opencode_support':bad},headers=self.csrf)
            self.assertEqual(response.status_code,400,response.text)
        self.assertEqual((await self.client.patch('/admin/api/unified/connections/oc',json={'opencode_support':True})).status_code,403)
        changed=await self.client.patch('/admin/api/unified/connections/oc',json={'opencode_support':True},headers=self.csrf)
        self.assertTrue(changed.json()['ok'])
        rows=(await self.client.get('/admin/api/unified/overview')).json()['connections']
        self.assertTrue(next(row for row in rows if row['id']=='oc')['opencode_support'])
        reloaded=Connections(self.app.state.connections.path)
        self.assertTrue(reloaded.get('oc')['opencode_support'])
        self.assertNotIn('synthetic-upstream-key',changed.text)

    async def test_explicit_session_and_real_user_agent_forward_without_client_secrets(self):
        await self.add()
        response=await self.chat(headers={'X-OpenCode-Session':'synthetic-session','User-Agent':'SyntheticAgent/1.2',
            'Cookie':'private=synthetic-cookie','X-Api-Key':'synthetic-client-secret','X-Unknown':'private-extra'})
        self.assertEqual(response.status_code,200,response.text)
        out=self.calls[-1].headers
        self.assertEqual(out['x-opencode-session'],'synthetic-session')
        self.assertEqual(out['user-agent'],'SyntheticAgent/1.2')
        self.assertEqual(out['authorization'],'Bearer synthetic-upstream-key')
        for name in ('cookie','x-api-key','x-unknown'):self.assertNotIn(name,out)

    async def test_native_session_headers_are_accepted(self):
        await self.add()
        for name in ('session_id','x-session-id','x-deepseek-harness-session-id'):
            await self.chat(headers={name:'synthetic-native-session','User-Agent':'SyntheticAgent/1.2'})
            self.assertEqual(self.calls[-1].headers['x-opencode-session'],'synthetic-native-session')
            self.assertEqual(self.calls[-1].headers[name],'synthetic-native-session')

    async def test_fallback_stays_stable_across_turns_and_distinguishes_new_context(self):
        await self.add()
        await self.chat();first=self.calls[-1].headers.get('x-opencode-session')
        self.assertTrue(first)
        await self.chat(messages=[{'role':'user','content':'Synthetic first prompt'},
            {'role':'assistant','content':'Synthetic answer'},{'role':'user','content':'Synthetic next turn'}])
        self.assertEqual(self.calls[-1].headers['x-opencode-session'],first)
        await self.chat(messages=[{'role':'user','content':'Different synthetic conversation'}])
        self.assertNotEqual(self.calls[-1].headers['x-opencode-session'],first)
        self.assertIn('Unified2API/',self.calls[-1].headers['user-agent'])

    async def test_disabled_support_does_not_add_session_headers(self):
        await self.add(opencode_support=False)
        await self.chat(headers={'X-OpenCode-Session':'synthetic-session'})
        self.assertNotIn('x-opencode-session',self.calls[-1].headers)

    async def test_responses_previous_response_id_keeps_fallback_session(self):
        await self.add()
        response=await self.client.post('/v1/responses',headers=self.auth,json={'model':'oc/model','input':'Synthetic start'})
        self.assertEqual(response.status_code,200,response.text)
        first=self.calls[-1].headers.get('x-opencode-session');self.assertTrue(first)
        await self.client.post('/v1/responses',headers=self.auth,json={'model':'oc/model',
            'previous_response_id':response.json()['id'],'input':'Synthetic follow-up'})
        self.assertEqual(self.calls[-1].headers['x-opencode-session'],first)

    async def test_console_test_also_receives_session(self):
        await self.add()
        response=await self.client.post('/admin/api/unified/test',headers=self.csrf,json={'model':'oc/model','message':'Synthetic probe'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(self.calls[-1].headers.get('x-opencode-session'))

    async def test_session_isolated_between_gateway_keys(self):
        await self.add();await self.chat()
        first=self.calls[-1].headers.get('x-opencode-session');self.assertTrue(first)
        key=(await self.client.post('/admin/api/keys',headers=self.csrf,json={'name':'Synthetic extra'})).json()
        token=key.get('key') or key.get('token')
        self.assertTrue(token,key)
        await self.chat(headers={'Authorization':'Bearer '+token})
        self.assertNotEqual(self.calls[-1].headers['x-opencode-session'],first)

    async def test_streaming_responses_capture_id_for_continuation(self):
        await self.add()
        def stream(request):
            self.calls.append(request)
            events=[{'type':'response.created','response':{'id':'resp-stream-synthetic','object':'response','status':'in_progress'}},
                {'type':'response.completed','response':{'id':'resp-stream-synthetic','object':'response','status':'completed','output':[]}}]
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events))
        self.app.state.connections.transport=httpx.MockTransport(stream)
        response=await self.client.post('/v1/responses',headers=self.auth,json={'model':'oc/model','input':'Synthetic stream start','stream':True})
        self.assertEqual(response.status_code,200,response.text)
        first=self.calls[-1].headers['x-opencode-session']
        self.app.state.connections.transport=httpx.MockTransport(self.upstream)
        await self.client.post('/v1/responses',headers=self.auth,json={'model':'oc/model','input':'Synthetic next',
            'previous_response_id':'resp-stream-synthetic'})
        self.assertEqual(self.calls[-1].headers['x-opencode-session'],first)

    async def test_fallback_survives_reload_without_storing_prompt_text(self):
        await self.add();await self.chat()
        first=self.calls[-1].headers['x-opencode-session']
        support=self.app.state.connections.opencode
        from unified.opencode import OpenCodeSupport
        other=OpenCodeSupport(support.key_path.parent)
        headers,_=other.prepare('oc',{'headers':[(b'authorization',b'Bearer synthetic-public')]},
            json.dumps({'messages':[{'role':'user','content':'Synthetic first prompt'}]}).encode())
        self.assertEqual(headers['x-opencode-session'],first)
        self.assertEqual(len(support.key_path.read_bytes()),32)
        self.assertNotIn(b'Synthetic first prompt',support.key_path.read_bytes())

    async def test_invalid_session_header_rejected_before_upstream(self):
        await self.add()
        response=await self.chat(headers={'X-OpenCode-Session':'x'*513})
        self.assertEqual(response.status_code,400,response.text)
        self.assertEqual(self.calls,[])
