import json
from urllib.parse import parse_qs, urlsplit

import httpx
import test_connections


class MonkeyLoginTest(test_connections.ConnectionsTest):
    # Reuse the isolated backend fixture, without duplicating inherited tests.
    test_crud_persistence_validation_and_csrf = None
    test_custom_first_chunk_and_disconnect_cleanup = None
    test_discovery_routing_headers_and_disable = None
    test_monkey_routing_and_protocol_rejection = None
    test_stream_reasoning_and_upstream_errors = None

    async def start_flow(self):
        response = await self.client.post('/admin/api/unified/monkeycode/login', headers=self.csrf, json={'name':'web account'})
        self.assertEqual(response.status_code, 200, response.text)
        data=response.json()
        token=parse_qs(urlsplit(data['launch_url']).query)['token'][0]
        return '/admin/api/unified/monkeycode/login/'+data['id'], {'Authorization':'Bearer '+token}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.client.aclose()
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost:8080')
        login=await self.client.post('/admin/api/login',json={'key':'long-admin-key-for-tests'})
        self.csrf={'X-CSRF-Token':login.json()['csrf']}

    async def test_handoff_validation_and_one_time_import(self):
        imported=[]
        async def native(method,path,body=b''):
            imported.append(json.loads(body))
            return 200, {'id':'user-1','ok':True}
        self.native.request=native
        path, auth=await self.start_flow()
        self.assertEqual((await self.client.post(path+'/helper')).status_code,403)
        self.assertEqual((await self.client.post(path+'/helper',headers=auth)).json()['state'],'waiting')
        self.assertEqual((await self.client.post(path+'/helper',headers=auth)).status_code,409)
        status=await self.client.get(path)
        self.assertNotIn('token',status.text)
        self.assertEqual((await self.client.post(path+'/complete',headers=auth,json={'cookie':'bad\nvalue'})).status_code,400)
        response=await self.client.post(path+'/complete',headers=auth,json={'cookie':'monkeycode_ai_session=private'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(imported,[{'cookie':'monkeycode_ai_session=private','name':'web account'}])
        self.assertEqual((await self.client.get(path)).json()['state'],'success')
        self.assertEqual((await self.client.post(path+'/complete',headers=auth,json={'cookie':'monkeycode_ai_session=private'})).status_code,409)
        self.assertNotIn('private',(await self.client.get(path)).text)

    async def test_cancel_expiry_owner_and_logout(self):
        path,auth=await self.start_flow()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost:8080') as other:
            await other.post('/admin/api/login',json={'key':'long-admin-key-for-tests'})
            self.assertEqual((await other.get(path)).status_code,404)
        self.assertEqual((await self.client.delete(path)).status_code,403)
        await self.client.delete(path,headers=self.csrf)
        self.assertEqual((await self.client.post(path+'/helper',headers=auth)).status_code,404)
        path,auth=await self.start_flow()
        self.app.state.monkey_login.flows[path.rsplit('/',1)[1]]['expires']=0
        self.assertEqual((await self.client.get(path+'/helper',headers=auth)).status_code,404)
        path,auth=await self.start_flow()
        await self.client.post('/admin/api/logout',headers=self.csrf)
        self.assertEqual((await self.client.get(path+'/helper',headers=auth)).status_code,401)

    async def test_failed_validation_and_window_close(self):
        async def native(*args):return 400,{'detail':'upstream rejected secret cookie'}
        self.native.request=native
        path,auth=await self.start_flow()
        await self.client.post(path+'/helper',headers=auth)
        response=await self.client.post(path+'/complete',headers=auth,json={'cookie':'secret'})
        self.assertEqual(response.status_code,502)
        self.assertNotIn('secret',response.text)
        self.assertEqual((await self.client.get(path)).json()['state'],'failed')
        path,auth=await self.start_flow()
        await self.client.post(path+'/helper',headers=auth)
        await self.client.post(path+'/cancel',headers=auth)
        self.assertEqual((await self.client.get(path)).json()['state'],'cancelled')
