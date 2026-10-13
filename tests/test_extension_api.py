import tempfile
import unittest
from unittest.mock import AsyncMock
import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app


class Native:
    async def request(self,*args): return 200,{'accounts':[],'data':[]}


class ExtensionAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        buddy=create_buddy(root=self.tmp.name+'/manage',auth_dir=self.tmp.name+'/auth',
            initial_key='synthetic-public',admin_key='synthetic-admin-long-key',secure_cookie=False)
        self.app=create_app(native=Native(),buddy=buddy)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        login=await self.client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.csrf={'X-CSRF-Token':login.json()['csrf']}
        self.public={'Authorization':'Bearer synthetic-public'}

    async def asyncTearDown(self):
        await self.client.aclose(); self.tmp.cleanup()

    async def test_messages_count_tokens_is_estimated_and_requires_key(self):
        body={'model':'workbuddy/deepseek-v4.1-flash','messages':[{'role':'user','content':'synthetic text'}]}
        response=await self.client.post('/v1/messages/count_tokens',headers=self.public,json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['estimated'])
        self.assertGreater(response.json()['input_tokens'],0)
        denied=await self.client.post('/v1/messages/count_tokens',json=body)
        self.assertEqual(denied.status_code,401)

    async def test_task_actions_require_csrf_and_invalid_provider_is_rejected(self):
        center=self.app.state.task_center
        center.run=AsyncMock(return_value={'ok':True,'earned_credit':0,'state':'already_claimed'})
        path='/admin/api/unified/tasks/qoder/one/pro_claim'
        denied=await self.client.post(path,json={})
        self.assertEqual(denied.status_code,403)
        self.assertEqual(center.run.await_count,0)
        result=await self.client.post(path,headers=self.csrf,json={})
        self.assertEqual(result.status_code,200)
        center.run.assert_awaited_once_with('qoder','one','pro_claim')

    async def test_workbuddy_provider_settings_reject_unknown_or_negative_fields(self):
        path='/admin/api/unified/provider-settings/workbuddy'
        self.assertEqual((await self.client.patch(path,headers=self.csrf,json={'password':'bad'})).status_code,400)
        self.assertEqual((await self.client.patch(path,headers=self.csrf,json={'daily_token_limit':-1})).status_code,400)

    async def test_bulk_preview_is_redacted_and_apply_imports_every_qoder_row(self):
        path='/admin/api/unified/accounts/import'
        body={'provider':'qoder','credentials':[{'uid':'one','accessToken':'synthetic-private-token'},
             {'uid':'two','accessToken':'synthetic-private-token-two'}],'preview':True}
        preview=await self.client.post(path,headers=self.csrf,json=body)
        self.assertEqual(preview.status_code,200,preview.text)
        self.assertNotIn('synthetic-private-token',preview.text)
        self.assertEqual(len(preview.json()['rows']),2)
        result=await self.client.post(path,headers=self.csrf,json={**body,'preview':False})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['imported'],2)
        rows=await self.app.state.qoder_accounts.accounts()
        self.assertEqual({row['uid'] for row in rows},{'one','two'})
        repeat=await self.client.post(path,headers=self.csrf,json={**body,'preview':False})
        self.assertEqual(repeat.json()['skipped'],2)
