import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI, Request
from admin.server import Store
from unified.bulk_import import BulkImport


def credential(enterprise, token='synthetic-access'):
    return {'auth': {'accessToken': token, 'refreshToken': 'synthetic-refresh',
                     'expiresAt': 4102444800000},
            'account': {'uid': 'same-user', 'enterpriseId': enterprise}}


class BulkIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_enterprises_remain_distinct_in_preview_skip_and_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Store, 'sync_active'):
            store = Store(Path(temporary)/'data', Path(temporary)/'auth', '', 'synthetic-admin-key-long')
            first = store.save_browser_account(credential('enterprise-a'), 'A')['account_id']
            store.data['accounts'][first]['enabled'] = False
            importer = BulkImport(FastAPI(), store, None, None, None, None)
            req = Request({'type': 'http', 'headers': []})
            body = {'provider': 'codebuddy', 'credentials': [credential('enterprise-b')]}
            preview = await importer.execute(body, req)
            self.assertFalse(preview['rows'][0]['exists'])
            result = await importer.execute({**body, 'preview': False}, req)
            self.assertEqual(result['imported'], 1)
            self.assertEqual(len(store.data['accounts']), 2)
            second = next(aid for aid in store.data['accounts'] if aid != first)
            before = store.file_for(store.data['accounts'][first]).read_bytes()
            skipped = await importer.execute({**body, 'preview': False}, req)
            self.assertEqual(skipped['skipped'], 1)
            updated = await importer.execute({**body, 'preview': False, 'overwrite': True,
                'credentials': [credential('enterprise-b', 'synthetic-new-token')]}, req)
            self.assertEqual(updated['results'][0]['state'], 'updated')
            self.assertEqual(store.file_for(store.data['accounts'][first]).read_bytes(), before)
            self.assertFalse(store.data['accounts'][first]['enabled'])
            self.assertEqual(json.loads(store.file_for(store.data['accounts'][second]).read_text())['auth']['accessToken'],
                             'synthetic-new-token')

    async def test_same_batch_skips_only_same_enterprise(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Store, 'sync_active'):
            store = Store(Path(temporary)/'data', Path(temporary)/'auth', '', 'synthetic-admin-key-long')
            importer = BulkImport(FastAPI(), store, None, None, None, None)
            req = Request({'type': 'http', 'headers': []})
            result = await importer.execute({'provider': 'codebuddy', 'preview': False,
                'credentials': [credential('a'), credential('b'), credential('a')]}, req)
            self.assertEqual([row['state'] for row in result['results']], ['imported', 'imported', 'skipped'])
            self.assertEqual(len(store.data['accounts']), 2)

    async def test_missing_existing_credential_does_not_block_other_imports(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Store, 'sync_active'):
            store = Store(Path(temporary)/'data', Path(temporary)/'auth', '', 'synthetic-admin-key-long')
            aid=store.save_browser_account(credential('a'),'missing')['account_id']
            store.file_for(store.data['accounts'][aid]).unlink()
            importer=BulkImport(FastAPI(),store,None,None,None,None)
            req=Request({'type':'http','headers':[]})
            body={'provider':'codebuddy','credentials':[credential('b')]}
            self.assertFalse((await importer.execute(body,req))['rows'][0]['exists'])
            result=await importer.execute({**body,'preview':False},req)
            self.assertEqual(result['imported'],1)
            self.assertIn(aid,store.data['accounts'])

    async def test_monkey_cookie_policy_and_native_skip_result_are_preserved(self):
        app = FastAPI()
        received = []
        @app.post('/admin/api/unified/monkeycode/accounts')
        async def account(req: Request):
            received.append(await req.json())
            return {'id': 'resolved-user', 'ok': True, 'skipped': True}
        class Native:
            async def request(self, *args):
                return 200, {'accounts': []}
        class API:
            native = Native()
        importer = BulkImport(app, None, None, None, None, API())
        result = await importer.execute({'provider': 'monkeycode', 'preview': False,
            'credentials': [{'cookie': 'synthetic-cookie'}]}, Request({'type': 'http', 'headers': []}))
        self.assertIs(received[0].get('overwrite'), False)
        self.assertEqual(received[0]['cookie'], 'synthetic-cookie')
        self.assertEqual(result['skipped'], 1)
