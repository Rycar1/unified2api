import tempfile
import unittest
from unittest.mock import AsyncMock

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app


class Native:
    def __init__(self):
        self.calls = []

    async def request(self, method, path, body=b''):
        self.calls.append((method, path))
        return 200, {'ok': True, 'data': [], 'accounts': [{'uid': 'one', 'enabled': True, 'disabled': False}] if path == '/admin/api/accounts' else []}


class OperationsAPI(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        buddy = create_buddy(root=self.temp.name + '/management', auth_dir=self.temp.name + '/auth',
                             initial_key='synthetic-public', admin_key='synthetic-admin-long-key', secure_cookie=False)
        self.native = Native()
        self.app = create_app(native=self.native, buddy=buddy)
        self.app.state.workbuddy_login.public_accounts = AsyncMock(return_value=[])
        self.app.state.workbuddy_login.models = AsyncMock(return_value=[])
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        login = await self.client.post('/admin/api/login', json={'key': 'synthetic-admin-long-key'})
        self.csrf = {'X-CSRF-Token': login.json()['csrf']}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_pool_crud_and_balance_never_expose_secret(self):
        root = '/admin/api/unified/connections'
        body = {'id': 'example', 'name': 'Test', 'base_url': 'https://example.test/v1',
                'key': 'synthetic-secret-one', 'models': ['m'], 'balance_scheme': 'deepseek'}
        self.assertEqual((await self.client.post(root, headers=self.csrf, json=body)).status_code, 200)
        path = root + '/example/accounts'
        self.assertEqual((await self.client.post(path, json={'name': 'Bad', 'key': 'secret'})).status_code, 403)
        added = await self.client.post(path, headers=self.csrf, json={'name': 'Two', 'key': 'synthetic-secret-two'})
        self.assertEqual(added.status_code, 200, added.text)
        aid = added.json()['id']
        listed = await self.client.get(path)
        self.assertEqual(len(listed.json()['accounts']), 2)
        self.assertNotIn('synthetic-secret', listed.text)
        changed = await self.client.patch(path + '/' + aid, headers=self.csrf, json={'name': 'Changed', 'key': ''})
        self.assertEqual(changed.json()['name'], 'Changed')
        self.app.state.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
            'is_available': True, 'balance_infos': [{'currency': 'USD', 'total_balance': '2.5'}]}))
        result = await self.client.post(path + '/' + aid + '/balance', headers=self.csrf, json={})
        self.assertEqual(result.json()['remaining'], 2.5)
        self.assertEqual((await self.client.delete(path + '/' + aid, headers=self.csrf)).status_code, 200)

    async def test_plan_preview_and_network_settings_are_authenticated(self):
        root = '/admin/api/unified'
        self.assertEqual((await self.client.patch(root + '/network', json={'idle_seconds': 50})).status_code, 403)
        changed = await self.client.patch(root + '/network', headers=self.csrf, json={'idle_seconds': 50})
        self.assertEqual(changed.json()['settings']['idle_seconds'], 50)
        route = {'id': 'test', 'targets': ['codebuddy/glm-5.3'], 'strategy': 'smart'}
        # Use an actual catalog model, without invoking it.
        models = (await self.client.get('/v1/models', headers={'Authorization': 'Bearer synthetic-public'})).json()['data']
        route['targets'] = [next(m['id'] for m in models if m['id'].startswith('codebuddy/'))]
        self.assertEqual((await self.client.post(root + '/routes', headers=self.csrf, json=route)).status_code, 200)
        result = await self.client.get(root + '/routes/test/plan')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['items'][0]['state'], 'unavailable')

    async def test_scoped_signin_only_visits_requested_platform(self):
        root = '/admin/api/unified/automation'
        config = {'auto_checkin': True, 'schedules': [
            {'id': 'trae-only', 'time': '09:00', 'providers': ['trae'], 'enabled': True}]}
        self.assertEqual((await self.client.patch(root, headers=self.csrf, json=config)).status_code, 200)
        self.native.calls.clear()
        result = await self.client.post(root + '/plans/trae-only/run', headers=self.csrf, json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()['record']['ok'])
        self.assertEqual(self.native.calls, [('GET', '/admin/api/accounts'), ('POST', '/admin/api/accounts/one/checkin')])
        status = (await self.client.get(root)).json()
        self.assertTrue(status['schedules'][0]['last_ok'])
        self.assertEqual(status['history'][0]['label'], '09:00')
