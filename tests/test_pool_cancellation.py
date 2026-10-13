import asyncio
import threading
from types import SimpleNamespace
import unittest
from admin.pool import PoolMiddleware


class PoolCancellation(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_during_credential_preparation_releases_account(self):
        entered, finish = threading.Event(), threading.Event()
        active = [0]
        def credentials():
            entered.set()
            finish.wait(3)
        def select(*args):
            active[0] += 1
            return 'synthetic-account',SimpleNamespace(get_headers=credentials)
        def release(aid):
            active[0] -= 1
        pool = SimpleNamespace(store=SimpleNamespace(check_api=lambda *args:None),
                               select=select,release=release)
        async def unexpected(*args):
            self.fail('request reached provider before credentials were prepared')
        scope = {'type':'http','method':'POST','path':'/v1/chat/completions','headers':[]}
        task = asyncio.create_task(PoolMiddleware(unexpected,pool)(scope,unexpected,unexpected))
        try:
            while not entered.is_set():
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(active[0],0)
        finally:
            finish.set()
