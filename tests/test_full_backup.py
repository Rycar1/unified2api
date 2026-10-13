import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
import shutil
import os
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

    def test_all_destinations_are_checked_before_any_existing_data_is_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            sources={name:root/'source'/name for name in ('management','codebuddy-auth')}
            targets={name:root/'target'/name for name in sources}
            for name in sources:
                sources[name].mkdir(parents=True)
                targets[name].mkdir(parents=True)
                (sources[name]/'account.json').write_text('new-'+name)
                (targets[name]/'account.json').write_text('old-'+name)
            outside=root/'outside'; outside.mkdir()
            (outside/'keep').write_text('outside-data')
            (targets['codebuddy-auth']/'external').symlink_to(outside, target_is_directory=True)
            blob=BackupManager(sources).export('test-password-long')
            with self.assertRaises(HTTPException) as error:
                BackupManager(targets).restore(base64.b64encode(blob).decode(),'test-password-long')
            self.assertEqual(error.exception.status_code,400)
            for name in sources:
                self.assertEqual((targets[name]/'account.json').read_text(),'old-'+name)
            self.assertEqual((outside/'keep').read_text(),'outside-data')

    def test_copy_failure_rolls_back_every_changed_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            sources={name:root/'source'/name for name in ('management','codebuddy-auth')}
            targets={name:root/'target'/name for name in sources}
            for name in sources:
                sources[name].mkdir(parents=True)
                targets[name].mkdir(parents=True)
                (sources[name]/'new.json').write_text('new-'+name)
                (targets[name]/'old.json').write_text('old-'+name)
            (targets['management']/'releases').mkdir()
            (targets['management']/'releases/live.json').write_text('release-state')
            blob=BackupManager(sources).export('test-password-long')
            copytree=shutil.copytree
            def interrupted(source,destination,*args,**kwargs):
                if Path(source).name=='codebuddy-auth':
                    (Path(destination)/'partial.json').write_text('partial-write')
                    raise OSError('synthetic disk failure')
                return copytree(source,destination,*args,**kwargs)
            with patch('unified.features.shutil.copytree',side_effect=interrupted), self.assertRaises(HTTPException):
                BackupManager(targets).restore(base64.b64encode(blob).decode(),'test-password-long')
            for name in sources:
                self.assertEqual((targets[name]/'old.json').read_text(),'old-'+name)
                self.assertFalse((targets[name]/'new.json').exists())
                self.assertFalse((targets[name]/'partial.json').exists())
                self.assertFalse(list(targets[name].glob('.unified-rollback-*')))
            self.assertEqual((targets['management']/'releases/live.json').read_text(),'release-state')

    def test_failed_move_keeps_unmoved_originals_and_restores_previous_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            sources={name:root/'source'/name for name in ('management','codebuddy-auth')}
            targets={name:root/'target'/name for name in sources}
            for name in sources:
                sources[name].mkdir(parents=True)
                targets[name].mkdir(parents=True)
                (sources[name]/'new.json').write_text('new-'+name)
                for filename in ('one.json','two.json'):
                    (targets[name]/filename).write_text(name+'-'+filename)
            blob=BackupManager(sources).export('test-password-long')
            replace=os.replace
            moves=[]
            def interrupted(source,destination):
                if Path(source).parent==targets['codebuddy-auth']:
                    moves.append(Path(source).name)
                    if len(moves)==2:
                        raise OSError('synthetic rename failure')
                return replace(source,destination)
            with patch('unified.features.os.replace',side_effect=interrupted), self.assertRaises(HTTPException):
                BackupManager(targets).restore(base64.b64encode(blob).decode(),'test-password-long')
            self.assertEqual(len(moves),2)
            for name in sources:
                for filename in ('one.json','two.json'):
                    self.assertEqual((targets[name]/filename).read_text(),name+'-'+filename)
                self.assertFalse((targets[name]/'new.json').exists())
