"""International chat header compatibility, using synthetic accounts only."""
from concurrent.futures import ThreadPoolExecutor
import importlib
from pathlib import Path
import sys
import unittest
import uuid


class InternationalChatHeaders(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = str(Path(__file__).resolve().parents[1] / 'vendor' / 'workbuddy2api-hub')
        if source not in sys.path:
            sys.path.insert(0, source)
        cls.accounts = importlib.import_module('wb_accounts')

    def account(self, uid='synthetic-a', realm='intl'):
        return self.accounts.Account({'uid': uid, 'realm': realm, 'product': 'workbuddy',
            'accessToken': 'synthetic-token-' + uid, 'enterpriseId': 'synthetic-tenant'})

    def test_international_chat_has_complete_correlated_request_ids(self):
        headers = self.account().headers(purpose='chat')
        self.assertEqual(headers['X-IDE-Version'], '5.8.4')
        self.assertEqual(headers['User-Agent'], 'WorkBuddy/5.8.4 workbuddy-ai/5.8.4 CLI/2.161.0')
        self.assertEqual(headers['X-Product'], 'SaaS')
        self.assertEqual(uuid.UUID(headers['X-Conversation-ID']).version, 7)
        for name in ('X-Conversation-Request-ID', 'X-Conversation-Message-ID', 'X-Root-Request-ID', 'X-Request-ID'):
            self.assertRegex(headers[name], r'^[0-9a-f]{32}$')
            self.assertEqual(uuid.UUID(hex=headers[name]).version, 7)
        self.assertEqual(headers['X-Conversation-Request-ID'], headers['X-Root-Request-ID'])
        self.assertEqual(headers['X-Conversation-Message-ID'], headers['X-Request-ID'])
        self.assertNotEqual(headers['X-Request-ID'], headers['X-Root-Request-ID'])
        self.assertNotIn('X-Machine-ID', headers)
        self.assertNotIn('X-Session-ID', headers)
        self.assertEqual(headers['Authorization'], 'Bearer synthetic-token-synthetic-a')
        self.assertEqual(headers['X-User-Id'], 'synthetic-a')
        self.assertEqual(headers['X-Enterprise-Id'], 'synthetic-tenant')

    def test_trace_formats_share_the_same_trace_and_span(self):
        headers = self.account().headers(purpose='chat')
        trace, span = headers['X-Trace-ID'], headers['X-B3-SpanId']
        self.assertRegex(trace, r'^[0-9a-f]{32}$')
        self.assertRegex(span, r'^[0-9a-f]{16}$')
        self.assertEqual(headers['X-B3-TraceId'], trace)
        self.assertEqual(headers['traceparent'], f'00-{trace}-{span}-01')
        self.assertEqual(headers['b3'], f'{trace}-{span}-1')
        self.assertEqual(headers['X-Agent-Purpose'], 'conversation')
        self.assertEqual(headers['X-Agent-Type'], 'main')
        self.assertNotIn('X-B3-ParentSpanId', headers)

    def test_concurrent_accounts_keep_credentials_isolated_and_ids_unique(self):
        accounts = [self.account('synthetic-' + str(index % 2)) for index in range(16)]
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda account: account.headers(purpose='chat'), accounts))
        self.assertEqual(len({row['X-Conversation-ID'] for row in results}), len(results))
        self.assertEqual(len({row['X-Request-ID'] for row in results}), len(results))
        self.assertEqual(len({row['X-Trace-ID'] for row in results}), len(results))
        for account, headers in zip(accounts, results):
            self.assertEqual(headers['Authorization'], 'Bearer ' + account.access_token)
            self.assertEqual(headers['X-User-Id'], account.uid)
            self.assertFalse(hasattr(account, 'conversation_id'))

    def test_billing_retains_its_own_headers(self):
        headers = self.account().headers(purpose='billing')
        self.assertIn('X-Machine-ID', headers)
        self.assertIn('X-Session-ID', headers)
        self.assertNotIn('X-Conversation-ID', headers)
        self.assertNotIn('traceparent', headers)
        self.assertNotIn('5.8.4', headers.get('User-Agent', ''))

    def test_domestic_chat_keeps_existing_identity(self):
        headers = self.account(realm='cn').headers(purpose='chat')
        self.assertEqual(headers['X-IDE-Version'], '5.5.6')
        self.assertEqual(headers['X-Product'], 'WorkBuddy')
        self.assertIn('X-Machine-ID', headers)
        self.assertIn('X-Session-ID', headers)
        self.assertNotIn('traceparent', headers)


if __name__ == '__main__':
    unittest.main()
