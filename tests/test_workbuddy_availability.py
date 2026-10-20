import unittest
import tempfile
import os
from pathlib import Path
import httpx
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from fastapi import HTTPException
from unified.routing import dispatch
from unified.workbuddy_login import WorkBuddyLogin
from unified.diagnostics import CURRENT_TRACE,Trace


class WorkBuddyAvailability(unittest.IsolatedAsyncioTestCase):
    async def setup_request(self,failures,partial=False):
        self.calls=[];self.events=[]
        async def relay(row,path,body,scope,receive,send):
            aid=row['account_uid'];self.calls.append(aid)
            if partial:
                await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
                await send({'type':'http.response.body','body':b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n','more_body':True})
            if aid in failures:raise HTTPException(503,failures[aid])
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':b'{"choices":[{"message":{"content":"OK"}}]}','more_body':False})
        self.wb=WorkBuddyLogin(None,SimpleNamespace(relay_row=relay),'synthetic','synthetic')
        self.wb.accounts=AsyncMock(return_value=[{'uid':a,'realm':'intl','enabled':True,'credits':{'remain':50}} for a in ('a','b')])
        self.api=SimpleNamespace(workbuddy=self.wb,network=None)
        self.scope={'type':'http','method':'POST','path':'/v1/chat/completions','headers':[],'state':{}}
        async def receive():return {'type':'http.request','body':b'','more_body':False}
        async def send(event):self.events.append(event)
        trace=Trace('synthetic','synthetic','workbuddy/m');token=CURRENT_TRACE.set(trace)
        try:await dispatch(self.api,'workbuddy',self.scope,receive,send,b'{"model":"m"}')
        finally:CURRENT_TRACE.reset(token)
        self.trace=trace

    async def test_local_rejection_switches_account_without_false_rate_cooldown(self):
        await self.setup_request({'a':{'code':'account_model_daily_limit','message':'该模型今日额度用尽'}})
        self.assertEqual(self.calls,['a','b'])
        self.assertEqual(self.events[0]['status'],200)
        self.assertEqual(self.wb.route_limits,{})
        self.assertEqual(self.wb.sunk_until,{})
        self.assertFalse(any(self.wb.active.values()))
        self.assertEqual([a['account_id'] for a in self.trace.attempts],['a','b'])

    async def test_plain_upstream_503_is_not_treated_as_local_rejection(self):
        with self.assertRaises(HTTPException):await self.setup_request({'a':'upstream unavailable'})
        self.assertEqual(self.calls,['a'])

    async def test_all_local_rejections_report_reasons_without_loop(self):
        with self.assertRaises(HTTPException) as ctx:
            await self.setup_request({'a':{'code':'account_reserve_balance','message':'余额低于保留线'},
                                      'b':{'code':'account_credential_refresh','message':'凭据刷新失败'}})
        self.assertEqual(self.calls,['a','b'])
        self.assertEqual(ctx.exception.detail['code'],'account_unavailable')
        self.assertEqual(ctx.exception.detail['reasons'],{'account_reserve_balance':1,'account_credential_refresh':1})
        self.assertFalse(any(self.wb.active.values()))

    async def test_local_rejection_after_output_never_replays(self):
        with self.assertRaises(HTTPException):
            await self.setup_request({'a':{'code':'account_model_daily_limit','message':'limited'}},partial=True)
        self.assertEqual(self.calls,['a'])

    async def test_preflight_respects_reserve_and_daily_guards(self):
        wb=WorkBuddyLogin(None,None,'synthetic','synthetic')
        wb.accounts=AsyncMock(return_value=[{'uid':'a','enabled':True,'realm':'intl','reserveBlocked':True,'credits':{'remain':50}},
            {'uid':'b','enabled':True,'realm':'intl','dailyLimitBlocked':True,'credits':{'remain':50}},
            {'uid':'c','enabled':True,'realm':'intl','modelDailyTokenLimit':100,'modelDailyTokens':{'m':100},'credits':{'remain':50}}])
        ready,_=await wb.routing_accounts('m',set())
        self.assertEqual(ready,[])
        ready,_=await wb.routing_accounts('other',set())
        self.assertEqual([r['id'] for r in ready],['c'])

    async def test_private_bridge_retains_account_and_does_not_confirm_rejection(self):
        from unified.custom import Connections
        from unified.account_target import AccountTarget,CURRENT_ACCOUNT
        with tempfile.TemporaryDirectory() as tmp:
            connection=Connections(Path(tmp)/'connections.json',transport=httpx.MockTransport(lambda req:httpx.Response(503,
                headers={'X-Unified-Account-Rejection':'account_reserve_balance'},json={'error':{'message':'no usable account'}})))
            trace=Trace('synthetic','synthetic','workbuddy/m');trace.begin('workbuddy/m')
            trace_token=CURRENT_TRACE.set(trace);pin=AccountTarget('workbuddy','a');token=CURRENT_ACCOUNT.set(pin)
            try:
                with self.assertRaises(HTTPException) as ctx:
                    await connection.relay_row({'id':'workbuddy','enabled':True,'kind':'workbuddy_intl','adapter':'hub','account_uid':'a',
                        'base_url':'http://synthetic/v1','key':'synthetic'},'/v1/chat/completions',b'{"model":"m"}',{},None,None)
                self.assertEqual(ctx.exception.detail['code'],'account_reserve_balance')
                self.assertEqual(trace.attempts[-1]['account_id'],'a')
                self.assertFalse(pin.confirmed)
            finally:CURRENT_ACCOUNT.reset(token);CURRENT_TRACE.reset(trace_token)

    def test_native_guard_marks_rejection_and_clears_request_local_state(self):
        from unified.workbuddy_hooks import install
        class Account:
            uid='a';realm='intl';enabled=True;access_token='synthetic'
            def ready(self,model=None):return self.uid=='b'
            def throttle_wait(self,model=None):return 0
            def reserve_blocked(self):return self.uid=='a'
            def daily_limit_blocked(self):return False
            def credit_limit_blocked(self,model=None):return False
            def model_token_limit_blocked(self,model=None):return False
        a=Account();b=Account();b.uid='b'
        class Pool:
            accounts=[a,b]
            def get(self,aid):return {'a':a,'b':b}.get(aid)
            def pick_for_session(self,realm=None,session_key=None,exclude=None,model=None):return b
        pool=Pool()
        class Handler:
            def __init__(self,aid):self.headers={'X-Unified-Account-Uid':aid};self.sent={}
            def send_header(self,k,v):self.sent[k]=v
            def end_headers(self):pass
            def do_POST(self):
                selected=pool.pick_for_session(realm='intl',model='m');self.end_headers();return selected
        proxy=SimpleNamespace(wb_webtools=SimpleNamespace(set_logger=lambda *a:None),record_error=lambda *a,**kw:None,
            build_upstream_body=lambda payload:payload,Handler=Handler,wb_accounts=SimpleNamespace(AccountPool=Pool))
        install(proxy)
        with patch.dict(os.environ,WORKBUDDY_EMBEDDED='true'):
            rejected=Handler('a');self.assertIsNone(rejected.do_POST())
            self.assertEqual(rejected.sent['X-Unified-Account-Rejection'],'account_reserve_balance')
            self.assertNotIn('X-Unified-Account',rejected.sent)
            accepted=Handler('b');self.assertIs(accepted.do_POST(),b)
            self.assertNotIn('X-Unified-Account-Rejection',accepted.sent)
            self.assertEqual(accepted.sent['X-Unified-Account'],'b')
