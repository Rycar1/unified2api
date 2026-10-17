import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from unified.custom import Connections
from unified.opencode_runtime import OpenCodeRuntime
from unittest.mock import Mock


class RuntimeIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connections = Connections(Path(self.temp.name) / 'connections.json')
        self.runtime = SimpleNamespace(base_url='http://127.0.0.1:8789/v1', token='private-runtime-token', ensure=AsyncMock())
        self.connections.runtime = self.runtime
        self.body = {'id':'opencode-runtime', 'name':'OpenCode2API', 'kind':'opencode_runtime', 'models':['big-pickle']}

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def call(self, path, payload):
        events = []
        async def receive(): await asyncio.Event().wait()
        async def send(event): events.append(event)
        await self.connections.relay('opencode-runtime', path, json.dumps(payload).encode(),
            {'type':'http', 'method':'POST', 'path':path, 'asgi':{'spec_version':'2.0'},
             'headers':[(b'authorization',b'Bearer caller-secret')]}, receive, send)
        return events

    async def test_server_owns_runtime_address_and_credentials_and_public_view_redacts(self):
        self.connections.save({**self.body, 'base_url':'https://untrusted.example/v1', 'key':'user-value'})
        row = self.connections.get('opencode-runtime')
        self.assertEqual(row['base_url'], self.runtime.base_url)
        self.assertNotIn(self.runtime.token, self.connections.path.read_text(encoding='utf-8'))
        self.assertNotIn('user-value', self.connections.path.read_text(encoding='utf-8'))
        self.assertNotIn(self.runtime.token, json.dumps(self.connections.rows()))
        self.assertEqual(self.connections.rows()[0]['kind'], 'opencode_runtime')
        self.runtime.ensure.assert_not_awaited()

    async def test_discovery_lazy_start_strips_upstream_prefix_and_filters_other_providers(self):
        def upstream(request):
            self.assertEqual(request.headers['authorization'], 'Bearer private-runtime-token')
            return httpx.Response(200, json={'data':[{'id':'opencode/big-pickle'}, {'id':'opencode/glm-5.3-flash-free'}, {'id':'other/paid'}]})
        self.connections.transport = httpx.MockTransport(upstream)
        value = await self.connections.discover(self.body)
        self.assertEqual(value['models'], ['big-pickle','glm-5.3-flash-free'])
        self.runtime.ensure.assert_awaited_once()

    async def test_chat_responses_and_messages_forward_to_managed_runtime(self):
        captured = []
        def upstream(request):
            captured.append(request)
            self.assertEqual(request.headers['authorization'], 'Bearer private-runtime-token')
            self.assertNotIn('caller-secret', str(request.headers))
            if request.url.path.endswith('/messages'):
                return httpx.Response(200,json={'type':'message','content':[{'type':'text','text':'ok'}],'stop_reason':'end_turn'})
            if request.url.path.endswith('/responses'):
                return httpx.Response(200,json={'id':'resp_runtime','object':'response','status':'completed','output':[]})
            return httpx.Response(200,json={'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]})
        self.connections.transport = httpx.MockTransport(upstream)
        self.connections.save(self.body)
        for path in ('/v1/chat/completions','/v1/responses','/v1/messages'):
            payload = {'model':'big-pickle','messages':[{'role':'user','content':'synthetic'}],
                       'previous_response_id':'resp_prior','tools':[{'type':'function','name':'synthetic_tool'}]}
            events = await self.call(path, payload)
            self.assertEqual(events[0]['status'],200)
            self.assertEqual(json.loads(captured[-1].content), payload)
        self.assertEqual(len(captured),3)

    async def test_compact_rejected_without_starting_runtime(self):
        self.connections.save(self.body)
        with self.assertRaises(HTTPException) as error:
            await self.call('/v1/responses/compact', {'model':'big-pickle'})
        self.assertEqual(error.exception.status_code,400)
        self.runtime.ensure.assert_not_awaited()

    async def test_runtime_models_report_supported_conversion_and_not_compact(self):
        from unified.capabilities import ModelCapabilities
        self.connections.save(self.body)
        model = self.connections.models()[0]
        capabilities = ModelCapabilities(SimpleNamespace(get=lambda key, default: default)).describe(model)
        self.assertEqual(model['id'],'opencode-runtime/big-pickle')
        self.assertEqual(capabilities['protocols'], {'chat':'supported','responses':'converted','messages':'converted'})
        self.assertFalse(capabilities['compact'])
        routed = ModelCapabilities(SimpleNamespace(get=lambda key, default: default),self.connections)
        self.assertEqual(routed.describe({'id':'opencode-runtime/big-pickle'})['protocols']['messages'],'converted')

    async def test_runtime_cannot_be_turned_into_fake_multi_key_accounts(self):
        self.connections.save(self.body)
        with self.assertRaises(HTTPException):
            self.connections.save_account('opencode-runtime', {'name':'second','key':'fake-key'})

    async def test_minimal_environment_contains_no_gateway_credentials(self):
        runtime = OpenCodeRuntime(Path(self.temp.name)/'runtime')
        with patch.dict(os.environ, {'ADMIN_KEY':'private-admin','UNIFIED_API_KEY':'private-gateway','OPENCODE_API_KEY':'private-client'}):
            env = runtime.environment()
        for key in ('ADMIN_KEY','UNIFIED_API_KEY','OPENCODE_API_KEY'): self.assertNotIn(key,env)
        self.assertEqual(env['BIND_HOST'],'127.0.0.1')
        self.assertEqual(env['OPENCODE_DISABLE_TOOLS'],'true')
        self.assertEqual(env['OPENCODE_INTERNAL_ALLOWED_TOOLS'],'')
        self.assertEqual(env['OPENCODE_PROXY_OMIT_SYSTEM_PROMPT'],'false')
        self.assertEqual(env['HOME'],str(runtime.root/'home'))

    async def test_missing_runtime_fails_clearly_and_status_contains_no_secret(self):
        runtime = OpenCodeRuntime(Path(self.temp.name)/'runtime')
        with patch('unified.opencode_runtime.shutil.which', return_value=None):
            self.assertFalse(runtime.status()['available'])
            with self.assertRaises(HTTPException) as error: await runtime.ensure()
        self.assertEqual(error.exception.status_code,503)
        self.assertNotIn(runtime.token,json.dumps(runtime.status()))

    async def test_concurrent_first_calls_launch_one_process_and_close_it(self):
        runtime = OpenCodeRuntime(Path(self.temp.name)/'runtime')
        def launch(): runtime.process = Mock(poll=Mock(return_value=None))
        transport = httpx.MockTransport(lambda request: httpx.Response(200,json={'status':'ok','healthy':True}))
        client_class = httpx.AsyncClient
        with patch.object(runtime,'status',return_value={'available':True}), \
             patch.object(runtime,'launch',side_effect=launch) as spawn, \
             patch('unified.opencode_runtime.httpx.AsyncClient',side_effect=lambda **kw:client_class(transport=transport,**kw)):
            await asyncio.gather(*(runtime.ensure() for _ in range(4)))
        spawn.assert_called_once()
        with patch.object(runtime,'stop') as stop:
            await runtime.close()
        stop.assert_called_once()


class RuntimeAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from admin.server import create_app as create_buddy
        from unified.server import create_app
        self.temp = tempfile.TemporaryDirectory()
        class Native:
            async def request(self,*args,**kw): return 200,{'accounts':[],'data':[]}
            def close(self): pass
        buddy = create_buddy(root=self.temp.name+'/management',auth_dir=self.temp.name+'/auth',
            initial_key='synthetic-client',admin_key='synthetic-long-admin-key',secure_cookie=False)
        self.app = create_app(native=Native(),buddy=buddy)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.app.state.opencode_runtime.close()
        self.temp.cleanup()

    async def test_status_requires_admin_and_does_not_start_process(self):
        response = await self.client.get('/admin/api/unified/opencode-runtime')
        self.assertEqual(response.status_code,401)
        login = await self.client.post('/admin/api/login',json={'key':'synthetic-long-admin-key'})
        response = await self.client.get('/admin/api/unified/opencode-runtime')
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(response.json()['running'])
        self.assertNotIn(self.app.state.opencode_runtime.token,response.text)
        body = {'id':'opencode-runtime','name':'OpenCode2API','kind':'opencode_runtime','models':['opencode/big-pickle']}
        self.assertEqual((await self.client.post('/admin/api/unified/connections',json=body)).status_code,403)
        created = await self.client.post('/admin/api/unified/connections',json=body,headers={'X-CSRF-Token':login.json()['csrf']})
        self.assertEqual(created.status_code,200,created.text)
        catalog = (await self.client.get('/v1/models',headers={'Authorization':'Bearer synthetic-client'})).json()['data']
        row = next(r for r in catalog if r['id']=='opencode-runtime/big-pickle')
        self.assertEqual(row['capabilities']['protocols']['messages'],'converted')

    async def test_public_messages_endpoint_reaches_runtime(self):
        login = await self.client.post('/admin/api/login',json={'key':'synthetic-long-admin-key'})
        await self.client.post('/admin/api/unified/connections',headers={'X-CSRF-Token':login.json()['csrf']},
            json={'id':'opencode-runtime','name':'OpenCode2API','kind':'opencode_runtime','models':['big-pickle']})
        self.app.state.opencode_runtime.ensure = AsyncMock()
        calls = []
        def upstream(request):
            calls.append(request)
            return httpx.Response(200,json={'id':'msg_synthetic','type':'message','role':'assistant',
                'content':[{'type':'text','text':'synthetic'}], 'stop_reason':'end_turn',
                'usage':{'input_tokens':1,'output_tokens':1}})
        self.app.state.connections.transport = httpx.MockTransport(upstream)
        response = await self.client.post('/v1/messages',headers={'x-api-key':'synthetic-client'},
            json={'model':'opencode-runtime/big-pickle','max_tokens':32,'messages':[{'role':'user','content':'synthetic'}]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(calls[0].url.path,'/v1/messages')
        self.assertEqual(json.loads(calls[0].content)['model'],'big-pickle')
