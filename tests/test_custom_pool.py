import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

import httpx
from fastapi import HTTPException
from unified.custom import Connections


class CustomPools(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connections = Connections(Path(self.temp.name) / 'connections.json')
        self.connections.save({'id': 'custom', 'name': 'Pool', 'base_url': 'https://example.test/v1',
                               'key': 'synthetic-first-secret', 'models': ['m']})

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def call(self):
        events = []
        async def receive():
            await asyncio.Event().wait()
        async def send(event):
            events.append(event)
        await self.connections.relay('custom', '/v1/chat/completions', b'{"model":"m"}',
                                     {'type': 'http', 'method': 'POST', 'path': '/v1/chat/completions',
                                      'asgi': {'spec_version': '2.0'}}, receive, send)
        return b''.join(e.get('body', b'') for e in events)

    async def test_migrated_key_and_multiple_accounts_are_redacted_and_persisted(self):
        self.connections.save_account('custom', {'name': 'Second', 'key': 'synthetic-second-secret'})
        restarted = Connections(self.connections.path)
        public = restarted.rows()[0]
        self.assertEqual(len(public['accounts']), 2)
        self.assertNotIn('synthetic-first-secret', json.dumps(public))
        self.assertNotIn('synthetic-second-secret', json.dumps(public))
        self.assertTrue(all(a['has_key'] for a in public['accounts']))

    async def test_rate_limited_key_falls_back_and_stays_in_cooldown(self):
        self.connections.save_account('custom', {'name': 'Second', 'key': 'synthetic-second-secret'})
        calls = []
        def handler(request):
            key = request.headers['authorization']
            calls.append(key)
            return httpx.Response(429, text='limited synthetic-first-secret') if key.endswith('first-secret') else httpx.Response(
                200, json={'choices': [{'message': {'content': 'ok'}, 'finish_reason': 'stop'}]})
        self.connections.transport = httpx.MockTransport(handler)
        self.assertIn(b'ok', await self.call())
        self.assertIn(b'ok', await self.call())
        self.assertEqual(calls.count('Bearer synthetic-first-secret'), 1)
        self.assertEqual(calls.count('Bearer synthetic-second-secret'), 2)

    async def test_error_before_content_falls_back_but_never_replays_emitted_content(self):
        self.connections.save_account('custom', {'name': 'Second', 'key': 'synthetic-second-secret'})
        calls = []
        def handler(request):
            calls.append(request.headers['authorization'])
            if len(calls) == 1:
                return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                      content=b'event: error\ndata: {"error":{"message":"denied"}}\n\n')
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        self.connections.transport = httpx.MockTransport(handler)
        self.assertIn(b'ok', await self.call())
        self.assertEqual(len(calls), 2)

    async def test_disabling_account_and_blank_edit_keep_credential(self):
        account = self.connections.rows()[0]['accounts'][0]
        self.connections.save_account('custom', {'name': 'Renamed', 'key': '', 'enabled': False}, account['id'])
        self.assertEqual(self.connections.get('custom')['accounts'][0]['key'], 'synthetic-first-secret')
        with self.assertRaises(HTTPException) as caught:
            await self.call()
        self.assertEqual(caught.exception.status_code, 503)

    async def test_deepseek_balance_preserves_last_value_on_failure(self):
        self.connections.save({'balance_scheme': 'deepseek'}, 'custom')
        account = self.connections.rows()[0]['accounts'][0]
        self.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
            'is_available': True, 'balance_infos': [{'currency': 'CNY', 'total_balance': '12.34'}]}))
        result = await self.connections.balance('custom', account['id'])
        self.assertEqual(result['remaining'], 12.34)
        self.assertEqual(result['unit'], 'CNY')
        self.connections.transport = httpx.MockTransport(lambda r: httpx.Response(500, text='synthetic-first-secret'))
        with self.assertRaises(HTTPException) as caught:
            await self.connections.balance('custom', account['id'])
        self.assertNotIn('synthetic-first-secret', str(caught.exception.detail))
        self.assertEqual(self.connections.rows()[0]['accounts'][0]['balance']['remaining'], 12.34)

    async def test_newapi_uses_independent_balance_credentials_and_exact_units(self):
        self.connections.save({'balance_scheme': 'newapi', 'balance_key': 'synthetic-console-secret',
                               'balance_user_id': '42'}, 'custom')
        account = self.connections.rows()[0]['accounts'][0]
        def handler(request):
            self.assertEqual(request.url.path, '/api/user/self')
            self.assertEqual(request.headers['authorization'], 'Bearer synthetic-console-secret')
            self.assertEqual(request.headers['new-api-user'], '42')
            return httpx.Response(200, json={'success': True, 'data': {'quota': 750000, 'used_quota': 250000}})
        self.connections.transport = httpx.MockTransport(handler)
        result = await self.connections.balance('custom', account['id'])
        self.assertEqual(result['remaining'], 750000)
        self.assertEqual(result['unit'], 'quota')
        self.assertNotIn('synthetic-console-secret', json.dumps(self.connections.rows()))

    async def test_timeout_is_per_account_and_releases_the_failed_attempt(self):
        self.connections.save_account('custom', {'name': 'Second', 'key': 'synthetic-second-secret'})
        self.connections.network = SimpleNamespace(get=lambda: {'first_token_seconds': .15, 'idle_seconds': .15})
        cancelled = asyncio.Event()
        async def handler(request):
            if request.headers['authorization'].endswith('first-secret'):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return httpx.Response(200, json={'choices': [{'message': {'content': 'ok'}}]})
        self.connections.transport = httpx.MockTransport(handler)
        result = await asyncio.wait_for(self.call(), 1)
        self.assertIn(b'ok', result)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(all(a['active'] == 0 for a in self.connections.pool_inventory('custom')['candidates']))

    async def test_sub2api_accepts_both_quota_and_direct_balance_documents(self):
        self.connections.save({'balance_scheme': 'sub2api'}, 'custom')
        account = self.connections.rows()[0]['accounts'][0]
        for payload, expected in [({'quota': {'limit': 10, 'used': 3}, 'unit': 'USD'}, 7),
                                  ({'remaining': '8.5', 'unit': 'CNY'}, 8.5)]:
            self.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
            result = await self.connections.balance('custom', account['id'])
            self.assertEqual(result['remaining'], expected)
            self.assertEqual(result['unit'], payload['unit'])

    async def test_replacing_key_discards_the_old_accounts_exhausted_balance(self):
        self.connections.save({'balance_scheme': 'deepseek'}, 'custom')
        aid = self.connections.rows()[0]['accounts'][0]['id']
        self.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={
            'is_available': False, 'balance_infos': [{'currency': 'USD', 'total_balance': '0'}]}))
        await self.connections.balance('custom', aid)
        self.assertFalse(self.connections.pool_inventory('custom')['available'])
        self.connections.save_account('custom', {'key': 'synthetic-replacement-secret'}, aid)
        self.assertTrue(self.connections.pool_inventory('custom')['available'])
        self.assertNotIn('balance', self.connections.rows()[0]['accounts'][0])
