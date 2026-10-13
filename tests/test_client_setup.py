import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from unified.client_setup import build_bundle


class ClientSetup(unittest.TestCase):
    def test_archive_has_only_requested_key_and_restorable_scripts(self):
        row={'id':'route/test','capabilities':{'protocols':{'responses':'converted'}}}
        blob=build_bundle({'client':'codex','base_url':'https://gateway.example/v1','api_key':'synthetic-client-key','model':'route/test'},[row])
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            config=json.loads(archive.read('client-config.json'))
            self.assertEqual(config['api_key'],'synthetic-client-key')
            self.assertIn('restore.cmd',archive.namelist())
            self.assertIn('configure.py',archive.namelist())
            self.assertNotIn('synthetic-client-key',archive.read('wb_agents.py').decode())

    def test_client_apply_and_restore_preserve_original_config_exactly(self):
        import importlib.util
        path=Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub/wb_agents.py'
        spec=importlib.util.spec_from_file_location('synthetic_client_agents',path)
        agents=importlib.util.module_from_spec(spec); spec.loader.exec_module(agents)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); codex=root/'.codex'; codex.mkdir()
            original=b'# preserve my comment\nmodel = "original"\n'
            (codex/'config.toml').write_bytes(original)
            with patch.object(agents,'_codex_home',return_value=str(codex)):
                agents.integrate(str(root/'state'),'codex','https://gateway.example/v1','synthetic-client-key',
                    model='route/test',home=str(root))
                self.assertIn('wire_api = "responses"',(codex/'config.toml').read_text())
                agents.restore(str(root/'state'),'codex',home=str(root))
            self.assertEqual((codex/'config.toml').read_bytes(),original)
