import asyncio
import base64
import json
import tempfile
import unittest
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit, parse_qs

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app
from fastapi import HTTPException
from unified.account_target import CURRENT_ACCOUNT,AccountTarget


class Native:
    def __init__(self):self.calls=[];self.status=200;self.wrong=False
    async def request(self,method,path,body=b''):
        if path.endswith('/accounts'):
            return 200,{'accounts':[{'uid':x,'enabled':True,'disabled':False,'cooling':False} for x in ('a','b')]}
        return 200,{'data':[{'id':'m'}]}
    async def events(self,method,path,body=b''):
        aid=parse_qs(urlsplit(path).query).get('_unified_account_id',['default'])[0]
        self.calls.append(aid)
        await asyncio.sleep(.01)
        value={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]} if self.status==200 else {'error':{'message':'limited','code':'rate_limit_exceeded'}}
        yield {'status':self.status,'headers':{'Content-Type':['application/json'],'X-Unified-Account':['wrong' if self.wrong else aid]}}
        yield {'data':base64.b64encode(json.dumps(value).encode()).decode()}


class AccountProbe(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.native=Native()
        buddy=create_buddy(root=self.temp.name+'/management',auth_dir=self.temp.name+'/auth',initial_key='synthetic-client',admin_key='synthetic-admin-long-key',secure_cookie=False)
        self.app=create_app(native=self.native,buddy=buddy)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        r=await self.client.post('/admin/api/login',json={'key':'synthetic-admin-long-key'})
        self.csrf={'X-CSRF-Token':r.json()['csrf']}
    async def asyncTearDown(self):
        await self.client.aclose();self.temp.cleanup()
    async def probe(self,aid='a',**extra):
        return await self.client.post('/admin/api/unified/test',headers=self.csrf,json={
            'model':'trae/m','message':'Reply OK','account_provider':'trae','account_id':aid,**extra})
    async def test_pins_one_account_and_confirms_actual_selection(self):
        r=await self.probe();self.assertEqual(r.status_code,200)
        self.assertTrue(r.json()['ok'],r.text)
        self.assertEqual(r.json()['account_id'],'a');self.assertTrue(r.json()['account_confirmed'])
        self.assertGreaterEqual(r.json()['duration_ms'],0)
        self.assertEqual(self.native.calls,['a'])
    async def test_failure_does_not_fall_back_to_other_account(self):
        self.native.status=429;r=await self.probe()
        self.assertFalse(r.json()['ok']);self.assertEqual(r.json()['status'],429)
        self.assertEqual(self.native.calls,['a'])
    async def test_unknown_account_wrong_provider_and_route_rejected_before_call(self):
        self.assertEqual((await self.probe('missing')).status_code,404)
        self.assertEqual((await self.probe(model='codebuddy/m')).status_code,400)
        self.assertEqual((await self.probe(model='route/m')).status_code,400)
        self.assertEqual(self.native.calls,[])
    async def test_parallel_probes_are_isolated(self):
        results=await asyncio.gather(self.probe('a'),self.probe('b'))
        self.assertEqual([r.json().get('account_id') for r in results],['a','b'])
        self.assertTrue(all(r.json()['ok'] for r in results))
    async def test_public_query_cannot_pin_an_account(self):
        await self.client.post('/v1/chat/completions?_unified_account_id=a',headers={'Authorization':'Bearer synthetic-client'},
            json={'model':'trae/m','messages':[{'role':'user','content':'OK'}]})
        self.assertEqual(self.native.calls,['default'])
    async def test_admin_csrf_required(self):
        r=await self.client.post('/admin/api/unified/test',json={'model':'trae/m','message':'OK','account_provider':'trae','account_id':'a'})
        self.assertEqual(r.status_code,403);self.assertEqual(self.native.calls,[])

    async def test_actual_account_mismatch_cannot_be_reported_as_success(self):
        self.native.wrong=True
        r=await self.probe()
        self.assertFalse(r.json()['ok'],r.text)
        self.assertFalse(r.json()['account_confirmed'])

    async def test_invalid_provider_type_is_rejected(self):
        self.assertEqual((await self.probe(account_provider={})).status_code,400)

    async def test_monkeycode_uses_the_same_strict_native_pin(self):
        r=await self.probe(account_provider='monkeycode',model='monkeycode/m')
        self.assertTrue(r.json()['ok'],r.text);self.assertEqual(self.native.calls,['a'])

    async def test_workbuddy_passes_exact_uid_without_failover(self):
        wb=self.app.state.api.workbuddy
        wb.accounts=AsyncMock(return_value=[{'uid':x,'realm':'intl','enabled':True} for x in ('a','b')])
        calls=[]
        async def relay(row,*args):
            calls.append(row['account_uid']);raise HTTPException(429,'synthetic throttle')
        wb.connections.relay_row=relay
        r=await self.probe(account_provider='workbuddy_intl',model='workbuddy/m')
        self.assertFalse(r.json()['ok']);self.assertEqual(r.json()['status'],429)
        self.assertEqual(calls,['a'])


class PythonPinning(unittest.IsolatedAsyncioTestCase):
    async def test_domestic_pool_respects_exact_pin_even_in_manual_mode(self):
        from admin.pool import AccountPool
        rows=[{'id':x,'enabled':True,'status':'valid'} for x in ('a','b')]
        data={'accounts':{x:{'name':x} for x in ('a','b')},'pool':{'routing':'manual'},'active':'a','account_status':{}}
        store=SimpleNamespace(lock=threading.RLock(),data=data,save=lambda:None,
            account_rows=lambda:rows,manager_for=lambda aid,item:aid)
        pool=AccountPool(store)
        self.assertEqual(pool.select(account_id='b')[0],'b');pool.release('b')
        pool.update('b',cooldown_until=pool.clock()+60)
        with self.assertRaises(HTTPException):pool.select(account_id='b')
        self.assertEqual(pool.select()[0],'a');pool.release('a')

    async def test_qoder_failure_does_not_try_another_account(self):
        from unified.qoder_accounts_hub import QoderAccounts
        account=lambda aid:SimpleNamespace(unified_id=aid,uid=aid,realm='intl',enabled=True,
            cooldown_until=0,model_cooldowns={},nickname=aid,credits={},access_token='synthetic',
            refresh_token='',personal_token='',note_error=lambda *a,**kw:None)
        obj=object.__new__(QoderAccounts)
        obj.pool=SimpleNamespace(accounts=[account('a'),account('b')],_cursor=0)
        obj.active={};obj.locks={};obj.model_rows=lambda a:[{'key':'m'}]
        calls=[]
        async def chunks(a,*args):
            calls.append(a.uid);raise HTTPException(429,'limited')
            yield
        obj.transport=SimpleNamespace(refresh=AsyncMock(return_value=False),chunks=chunks)
        pin=CURRENT_ACCOUNT.set(AccountTarget('qoder','b'))
        try:
            with self.assertRaises(HTTPException):
                async for _ in obj.chat_chunks({'model':'m','messages':[{'role':'user','content':'OK'}]}):pass
        finally:CURRENT_ACCOUNT.reset(pin)
        self.assertEqual(calls,['b'])
        self.assertEqual(obj.active['b'],0)

    async def test_antigravity_failure_does_not_try_another_account(self):
        from collections import OrderedDict
        from unified.antigravity import AntigravityAccounts
        obj=object.__new__(AntigravityAccounts)
        obj.rows=[{'id':x,'name':x,'enabled':True,'project_id':'synthetic'} for x in ('a','b')]
        obj.signatures=OrderedDict();obj.active={};obj.cursor=0;obj.ensure_token=AsyncMock();obj.save=lambda:None
        calls=[]
        async def frames(row,*args):
            calls.append(row['id']);raise HTTPException(429,'limited')
            yield
        obj.generate_frames=frames
        pin=CURRENT_ACCOUNT.set(AccountTarget('antigravity','b'))
        try:
            with self.assertRaises(HTTPException):
                async for _ in obj.chat_chunks({'model':'gemini-test','messages':[{'role':'user','content':'OK'}]}):pass
        finally:CURRENT_ACCOUNT.reset(pin)
        self.assertEqual(calls,['b']);self.assertEqual(obj.active['b'],0)
