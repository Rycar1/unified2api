import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from unified.workbuddy_runtime import EmbeddedWorkBuddy


class EmbeddedRuntime(unittest.IsolatedAsyncioTestCase):
    async def test_private_runtime_health_auth_preservation_and_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            accounts = Path(tmp)/'accounts'
            accounts.mkdir()
            sentinel = accounts/'existing-data.txt'
            sentinel.write_text('keep existing volume data',encoding='utf-8')
            runtime = EmbeddedWorkBuddy('test-runtime-api-key','test-runtime-admin-key',accounts,Path(tmp)/'usage',port=0)
            async with runtime:
                address = runtime.server.server_address
                self.assertEqual(address[0],'127.0.0.1')
                async with httpx.AsyncClient(base_url='http://127.0.0.1:'+str(address[1]),trust_env=False) as client:
                    self.assertEqual((await client.get('/health')).status_code,200)
                    self.assertEqual((await client.get('/accounts')).status_code,401)
                    login = await client.post('/panel/login',json={'password':'test-runtime-admin-key'})
                    self.assertEqual(login.status_code,200,login.text)
                    result = await client.get('/accounts?realm=intl',headers={'X-Panel-Token':login.json()['token'],
                        'Authorization':'Bearer test-runtime-api-key'})
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertEqual(result.json()['accounts'],[])
                self.assertEqual(sentinel.read_text(encoding='utf-8'),'keep existing volume data')
            self.assertFalse(runtime.thread.is_alive())
            self.assertFalse(runtime.proxy.SCHEDULER._thread.is_alive())


if __name__=='__main__':
    unittest.main()
