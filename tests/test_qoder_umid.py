"""Offline checks for the official native component's build-time integrity gate."""
import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('umid_installer',Path(__file__).resolve().parents[1]/'vendor/qoder2api-hub/_install_umid.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class QoderUmid(unittest.TestCase):
    def test_registry_cannot_redirect_to_an_unreviewed_binary(self):
        valid = {'version':installer.PACKAGE_VERSION,'dist':{
            'tarball':installer.REGISTRY_URL+'/-/qodercli-'+installer.PACKAGE_VERSION+'.tgz',
            'integrity':installer.PACKAGE_INTEGRITY}}
        for field in ('version','tarball','integrity'):
            value = json.loads(json.dumps(valid))
            if field == 'version':
                value[field] = '99.0.0'
            else:
                value['dist'][field] = 'https://untrusted.invalid/component' if field == 'tarball' else 'sha512-unreviewed'
            with self.subTest(field=field),patch.object(installer.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(value).encode())):
                with self.assertRaises(RuntimeError):
                    installer.resolve_tarball()

    def test_corrupt_download_never_reaches_extraction_or_install(self):
        with patch.object(installer,'is_installed',return_value=False), \
             patch.object(installer,'resolve_tarball',return_value=(installer.PACKAGE_VERSION,'https://registry.npmjs.org/synthetic',installer.PACKAGE_INTEGRITY)), \
             patch.object(installer,'download_tarball',return_value=b'corrupt'), \
             patch.object(installer,'extract_runtime_bundle') as extract,patch.object(installer,'install') as install:
            self.assertEqual(installer.main(['--platform','linux','--arch','x64']),1)
        extract.assert_not_called()
        install.assert_not_called()

    def test_architecture_selection_cannot_install_arm_binary_on_x64(self):
        def elf(machine):
            header = bytearray(24)
            header[:6] = b'\x7fELF\x02\x01'
            struct.pack_into('<H',header,18,machine)
            return bytes(header)
        candidates = [(0,24,elf(installer.ELF_EM_AARCH64)),(24,48,elf(installer.ELF_EM_X86_64))]
        self.assertEqual(installer.select_candidate(candidates,'elf-x86_64')[0],1)
        self.assertFalse(installer.verify_component(candidates[0][2],'elf-x86_64'))
