import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.responses import JSONResponse
from unified.adaptive import AdaptiveRoutes
from unified.features import UnifiedConfig
from unified.model_aliases import ModelAliases
from unified.server import API
from unified.routing import inventory


class RouteRecovery(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store=SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None,
                              check_api=lambda *args:None)
        cfg=UnifiedConfig(store)
        self.routes=AdaptiveRoutes(cfg)
        targets=['trae/temporarily-hidden','trae/backup']
        self.routes.save({'id':'recover','targets':targets,'strategy':'priority'},set(targets))
        self.calls=[]
        calls=self.calls
        self.failed=False
        test=self
        class Upstream(API):
            async def stream_native(self,scope,receive,send,body):
                model=json.loads(body)['model'];calls.append(model)
                if test.failed and model=='temporarily-hidden':
                    result={'error':{'code':'model_not_found','message':'temporarily unavailable'}}
                    status=400
                    # Headers can arrive before an error body. Failover must
                    # preserve its classification rather than cancel it early.
                    await send({'type':'http.response.start','status':status,
                                'headers':[(b'content-type',b'application/json')]})
                    await asyncio.sleep(.01)
                    await send({'type':'http.response.body','body':json.dumps(result).encode(),'more_body':False})
                    return
                else:
                    result={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}
                    status=200
                return await JSONResponse(result,status)(scope,receive,send)
        self.api=Upstream(None,SimpleNamespace(state=SimpleNamespace(store=store)),None,routes=self.routes)
        self.api.aliases=ModelAliases(cfg)
        self.api.catalog_by_prefix={'trae':[{'id':'trae/backup'}]}
        self.snapshot={'trae':{'available':True}}
        self.inventory=patch('unified.routing.inventory',AsyncMock(side_effect=lambda api:self.snapshot))
        self.inventory.start()
        self.addCleanup(self.inventory.stop)

    async def invoke(self,model,stream=False):
        raw=json.dumps({'model':model,'messages':[],'stream':stream}).encode()
        used=False
        async def receive():
            nonlocal used
            if not used:
                used=True
                return {'type':'http.request','body':raw,'more_body':False}
            await asyncio.Event().wait()
        events=[]
        async def send(event):events.append(event)
        scope={'type':'http','method':'POST','path':'/v1/chat/completions',
               'headers':[],'query_string':b''}
        await self.api(scope,receive,send)
        return next(e['status'] for e in events if e['type']=='http.response.start'),scope

    async def test_configured_route_can_call_temporarily_unlisted_model_but_direct_typo_is_rejected(self):
        status,scope=await self.invoke('route/recover')
        self.assertEqual(status,200)
        self.assertEqual(self.calls,['temporarily-hidden'])
        self.assertEqual(scope['state']['resolved_model'],'trae/temporarily-hidden')
        self.assertEqual((await self.invoke('trae/temporarily-hidden'))[0],400)
        self.assertEqual(len(self.calls),1)

    async def test_hidden_model_failure_cools_then_recovers_without_editing_route(self):
        for stream in (False,True):
            with self.subTest(stream=stream):
                self.routes.health.clear();self.calls.clear();self.failed=True
                self.assertEqual((await self.invoke('route/recover',stream))[0],200)
                self.assertEqual(self.calls,['temporarily-hidden','backup'])
                self.assertGreater(self.routes.health['trae/temporarily-hidden']['cooldown_until'],0)
                self.calls.clear()
                await self.invoke('route/recover',stream)
                self.assertEqual(self.calls,['backup'])
                self.routes.health['trae/temporarily-hidden']['cooldown_until']=0
                self.calls.clear();self.failed=False
                await self.invoke('route/recover',stream)
                self.assertEqual(self.calls,['temporarily-hidden'])


class InventoryRecovery(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_requests_share_snapshot_and_cancellation_is_isolated(self):
        api=SimpleNamespace()
        entered,release=asyncio.Event(),asyncio.Event()
        async def refresh(api):
            entered.set();await release.wait()
            return {'trae':{'available':True}}
        with patch('unified.routing.refresh_inventory',AsyncMock(side_effect=refresh)) as load:
            first=asyncio.create_task(inventory(api))
            await entered.wait()
            second=asyncio.create_task(inventory(api))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            release.set()
            self.assertTrue((await second)['trae']['available'])
            self.assertEqual(load.await_count,1)

    async def test_failed_unrelated_platform_does_not_disable_route_snapshot(self):
        store=SimpleNamespace(lock=threading.RLock(),account_rows=lambda:[])
        pool=SimpleNamespace(rows=lambda rows:[])
        native=SimpleNamespace(request=AsyncMock(return_value=(200,{'accounts':[{'remaining':10}]})))
        api=SimpleNamespace(buddy=SimpleNamespace(state=SimpleNamespace(store=store,pool=pool)),
            native=native,qoder=SimpleNamespace(accounts=AsyncMock(side_effect=RuntimeError('unavailable'))),
            workbuddy=None,connections=None,antigravity=None)
        snapshot=await inventory(api)
        self.assertTrue(snapshot['trae']['available'])
        self.assertNotIn('qoder',snapshot)
        self.assertEqual(await inventory(api),snapshot)
        self.assertEqual(native.request.await_count,2)
