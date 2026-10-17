import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from fastapi import HTTPException
from admin.server import create_app as create_buddy
from unified.server import create_app
from unified.outbound import ProxySettings, install_workbuddy


class OutboundSettings(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = SimpleNamespace(root=Path(self.temp.name))
        self.env = patch.dict(os.environ, {'ANTIGRAVITY_PROXY':'', 'UNIFIED_OUTBOUND_PROXY':'', 'HTTPS_PROXY':'http://ambient.test:8123'})
        self.env.start(); self.addCleanup(self.env.stop)
        self.proxy = ProxySettings(self.store)

    def test_independent_does_not_change_other_providers_and_global_is_fallback(self):
        self.proxy.update({'antigravity_proxy':'http://private:secret@google.test:7890'})
        for name in ('codebuddy','workbuddy','trae','monkeycode','qoder','qoder-cn','custom'):
            self.assertIsNone(self.proxy.resolve(name))
        self.assertIn('google.test',self.proxy.resolve('antigravity'))
        self.proxy.update({'global_proxy':'http://global.test:7891'})
        self.assertIn('google.test',self.proxy.resolve('antigravity'))
        self.assertIn('global.test',self.proxy.resolve('qoder'))
        self.proxy.update({'antigravity_proxy':''})
        self.assertIn('global.test',self.proxy.resolve('antigravity'))
        self.proxy.update({'global_proxy':''})
        self.assertIsNone(self.proxy.resolve('antigravity'))
        self.assertFalse(self.proxy.options()['trust_env'])

    def test_persistence_is_encrypted_and_api_metadata_never_contains_secret(self):
        url='http://private:synthetic-secret@proxy.test:7890'
        self.proxy.update({'antigravity_proxy':url})
        self.assertNotIn('synthetic-secret',self.proxy.path.read_text())
        self.assertNotIn('proxy.test',self.proxy.path.read_text())
        self.assertNotIn('synthetic-secret',json.dumps(self.proxy.public()))
        self.assertEqual(ProxySettings(self.store).resolve('antigravity'),url)

    def test_invalid_update_is_atomic_and_native_failure_keeps_previous_settings(self):
        apply=Mock()
        self.proxy.apply_native=apply
        self.proxy.update({'global_proxy':'http://old.test:7890'})
        for value in ('socks5://host:1','http://host:99999','http://host/path','http://host?x=secret','http://host\n:80',3):
            with self.assertRaises(HTTPException):
                self.proxy.update({'global_proxy':value,'antigravity_proxy':'http://new.test:7890'})
        self.assertIsNone(self.proxy.configured('antigravity_proxy') or None)
        apply.side_effect=RuntimeError('synthetic failure')
        with self.assertRaises(RuntimeError): self.proxy.update({'global_proxy':'http://new.test:7890'})
        self.assertIn('old.test',self.proxy.resolve())

    def test_workbuddy_honors_account_proxy_bypasses_internal_and_uses_live_global(self):
        original=Mock(return_value='ok')
        module=SimpleNamespace(urlopen=original)
        install_workbuddy(module,self.proxy)
        self.proxy.update({'global_proxy':'http://global.test:7890','antigravity_proxy':'http://google.test:7891'})
        module.urlopen('https://provider.test')
        self.assertEqual(original.call_args.kwargs['proxy'],'http://global.test:7890')
        module.urlopen('https://provider.test',proxy='http://account.test:7892')
        self.assertEqual(original.call_args.kwargs['proxy'],'http://account.test:7892')
        module.urlopen('http://127.0.0.1:8788/health')
        self.assertEqual(original.call_args.kwargs['proxy'],'')
        self.proxy.update({'global_proxy':''})
        module.urlopen('https://provider.test')
        self.assertEqual(original.call_args.kwargs['proxy'],'')


class OutboundAPI(unittest.IsolatedAsyncioTestCase):
    async def test_auth_csrf_and_redacted_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            buddy=create_buddy(root=temp+'/management',auth_dir=temp+'/auth',initial_key='synthetic-public',admin_key='synthetic-admin-long-key',secure_cookie=False)
            native=SimpleNamespace(set_proxy=Mock())
            app=create_app(native=native,buddy=buddy)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                path='/admin/api/unified/proxies'
                self.assertEqual((await client.get(path)).status_code,401)
                login=await client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
                body={'antigravity_proxy':'http://user:synthetic-proxy-password@proxy.test:7890'}
                self.assertEqual((await client.patch(path,json=body)).status_code,403)
                result=await client.patch(path,json=body,headers={'X-CSRF-Token':login.json()['csrf']})
                self.assertEqual(result.status_code,200,result.text)
                self.assertTrue(result.json()['settings']['antigravity_proxy']['configured'])
                self.assertNotIn('synthetic-proxy-password',result.text)
                self.assertNotIn('proxy.test',(await client.get(path)).text)
                self.assertIsNone(app.state.outbound.resolve('workbuddy'))
                self.assertIn('proxy.test',app.state.outbound.resolve('antigravity'))
