import base64
import tempfile
import unittest
from pathlib import Path
from unified.features import BackupManager


class FullBackup(unittest.TestCase):
    def test_workbuddy_accounts_and_hidden_crypto_key_restore_without_usage_or_release_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            management=root/'management'; accounts=root/'accounts'; usage=root/'usage'
            for path in (management/'releases',accounts,usage): path.mkdir(parents=True)
            (management/'config.json').write_text('{"setting":1}')
            (management/'releases'/'previous.json').write_text('container-control-state')
            (accounts/'one.json').write_text('synthetic-account')
            (accounts/'.crypto-key').write_text('synthetic-crypto-key')
            (usage/'usage.jsonl').write_text('synthetic-history')
            backup=BackupManager({'management':management,'workbuddy-accounts':accounts,'workbuddy-usage':usage})
            blob=backup.export('test-password-long',False)
            destination=root/'restored'
            restored=BackupManager({name:destination/name for name in backup.roots})
            (destination/'management/releases').mkdir(parents=True)
            (destination/'management/releases/live.json').write_text('keep-live-release')
            restored.restore(base64.b64encode(blob).decode(),'test-password-long')
            self.assertEqual((destination/'workbuddy-accounts/one.json').read_text(),'synthetic-account')
            self.assertEqual((destination/'workbuddy-accounts/.crypto-key').read_text(),'synthetic-crypto-key')
            self.assertFalse((destination/'workbuddy-usage/usage.jsonl').exists())
            self.assertFalse((destination/'management/releases/previous.json').exists())
            self.assertEqual((destination/'management/releases/live.json').read_text(),'keep-live-release')
