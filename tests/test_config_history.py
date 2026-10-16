import json
from pathlib import Path
import tempfile
import unittest
from fastapi import HTTPException
from unified.config_history import ConfigHistory


class ConfigHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.history=ConfigHistory(self.root)

    def tearDown(self): self.temp.cleanup()

    def test_snapshots_are_encrypted_and_secret_changes_are_visible_but_redacted(self):
        before={'id':'service','key':'synthetic-very-private-one','accounts':[{'id':'a','key':'synthetic-account-secret'}]}
        after={**before,'key':'synthetic-very-private-two'}
        rid=self.history.record('connection','service',before,after)
        self.assertEqual(self.history.target(rid),before)
        detail=self.history.detail(rid,after)
        self.assertEqual(detail['changes'][0]['path'],'key')
        self.assertNotIn('synthetic-',json.dumps(detail))
        self.assertNotIn(b'synthetic-',(self.root/'config-history.sqlite').read_bytes())
        self.assertTrue(detail['fingerprint'])

    def test_pending_mutation_and_optimistic_restore_guard(self):
        rid=self.history.reserve('network','settings',{'idle_seconds':60})
        self.history.complete(rid,{'idle_seconds':90})
        detail=self.history.detail(rid,{'idle_seconds':90})
        self.history.verify(detail['fingerprint'],{'idle_seconds':90})
        with self.assertRaises(HTTPException) as err:
            self.history.verify(detail['fingerprint'],{'idle_seconds':120})
        self.assertEqual(err.exception.status_code,409)
        self.assertEqual(self.history.target(rid),{'idle_seconds':60})
        no_change=self.history.reserve('network','settings',{'idle_seconds':90})
        self.history.complete(no_change,{'idle_seconds':90})
        self.assertEqual(self.history.query()['total'],1)

    def test_history_key_survives_restart_and_missing_key_does_not_destroy_history(self):
        rid=self.history.record('route','a',None,{'id':'a','targets':['workbuddy/glm-5.3-flash']})
        self.assertEqual(ConfigHistory(self.root).target(rid,'after')['id'],'a')
        (self.root/'.config-history.key').unlink()
        with self.assertRaises(RuntimeError): ConfigHistory(self.root)

