"""Synthetic upstreams: quota failures must not poison funded sibling accounts."""
import asyncio
import json
import threading
import unittest
from types import SimpleNamespace

from admin.pool import AccountPool, PoolMiddleware, REQUEST_CREDENTIAL
from unified.diagnostics import CURRENT_TRACE, Trace
from unified.routing import dispatch
from fastapi import HTTPException
from unittest.mock import AsyncMock,patch


class AccountFailover(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now=1792400000
        self.rows=[{'id':aid,'enabled':True,'status':'valid'} for aid in ('a','b','c')]
        self.data={'accounts':{r['id']:{'name':r['id']} for r in self.rows},'active':'a',
                   'pool':{'routing':'round_robin'},'account_status':{}}
        store=SimpleNamespace(lock=threading.RLock(),data=self.data,save=lambda:None,
            account_rows=lambda:self.rows,manager_for=lambda aid,row:SimpleNamespace(id=aid,get_headers=lambda:{}),check_api=lambda *a:None)
        self.pool=AccountPool(store,clock=lambda:self.now)
        self.calls=[]

    async def run_request(self, statuses, stream=False, partial=False, routed=False):
        async def provider(scope,receive,send):
            aid=REQUEST_CREDENTIAL.get().id;self.calls.append(aid)
            status=statuses.get(aid,200)
            await send({'type':'http.response.start','status':200 if stream else status,
                        'headers':[(b'content-type',b'text/event-stream' if stream else b'application/json'),(b'retry-after',b'120')]})
            if partial:
                await send({'type':'http.response.body','body':b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n','more_body':True})
            value={'error':{'code':status,'message':'limited'}} if status>=400 else {'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}
            blob=json.dumps(value).encode()
            await send({'type':'http.response.body','body':b'data: '+blob+b'\n\n' if stream else blob,'more_body':False})
        class Gateway(SimpleNamespace):
            async def __call__(inner,scope,receive,send):
                value=json.loads((await receive())['body']);value['model']=value['model'].partition('/')[2]
                return await dispatch(inner,'codebuddy',scope,receive,send,json.dumps(value).encode())
        api=Gateway(buddy=PoolMiddleware(provider,self.pool),network=None)
        api.buddy.state=SimpleNamespace(pool=self.pool,store=self.pool.store)
        scope={'type':'http','method':'POST','path':'/v1/chat/completions','headers':[], 'state':{}}
        async def receive():return {'type':'http.request','body':b'', 'more_body':False}
        events=[]
        async def send(event):events.append(event)
        trace=Trace('synthetic','synthetic','codebuddy/m');token=CURRENT_TRACE.set(trace)
        try:
            if routed:
                from unified.adaptive import AdaptiveRoutes,availability
                from unified.features import UnifiedConfig
                from unified.routing import route_dispatch
                import time
                api.routes=AdaptiveRoutes(UnifiedConfig(self.pool.store))
                api.routes.save({'id':'test','targets':['codebuddy/m'],'strategy':'smart','retries':0},{'codebuddy/m'})
                api.inventory_cache=(time.monotonic(),{'codebuddy':availability(self.pool.rows(self.rows))})
                await route_dispatch(api,'test',scope,receive,send,{'model':'route/test','stream':stream})
            else:
                await dispatch(api,'codebuddy',scope,receive,send,json.dumps({'model':'m','stream':stream}).encode())
        finally:CURRENT_TRACE.reset(token)
        return events,trace

    async def test_partial_429s_try_each_account_until_success(self):
        events,trace=await self.run_request({'a':429,'b':429,'c':200})
        self.assertEqual(self.calls,['a','b','c'])
        self.assertEqual([e['status'] for e in events if e['type']=='http.response.start'],[200])
        self.assertNotIn(b'limited',b''.join(e.get('body',b'') for e in events))
        self.assertEqual([a.get('account_id') for a in trace.attempts],['a','b','c'])
        self.assertFalse(any(self.pool.active.values()))
        self.assertEqual(self.pool.state(self.rows[0]),'available')
        self.assertEqual(self.pool.state(self.rows[0],model='m'),'cooling')
        self.assertEqual(self.pool.state(self.rows[0],model='other'),'available')

    async def test_all_accounts_limited_stop_without_looping(self):
        events,_=await self.run_request({'a':429,'b':429,'c':429})
        self.assertEqual(self.calls,['a','b','c'])
        self.assertEqual(events[0]['status'],429)
        self.assertFalse(any(self.pool.active.values()))

    async def test_sse_error_before_output_can_switch_accounts(self):
        _,_=await self.run_request({'a':429,'b':200},stream=True)
        self.assertEqual(self.calls,['a','b'])

    async def test_started_stream_is_never_replayed(self):
        events,_=await self.run_request({'a':429},stream=True,partial=True)
        self.assertEqual(self.calls,['a'])
        self.assertIn(b'hello',b''.join(e.get('body',b'') for e in events))

    async def test_request_parameter_errors_do_not_switch(self):
        events,_=await self.run_request({'a':400})
        self.assertEqual(self.calls,['a']);self.assertEqual(events[0]['status'],400)

    async def test_zero_channel_retries_still_allows_account_failover(self):
        events,_=await self.run_request({'a':429,'b':429,'c':200},routed=True)
        self.assertEqual(self.calls,['a','b','c']);self.assertEqual(events[0]['status'],200)

    async def test_manual_account_setting_is_preserved(self):
        self.data['pool']['routing']='manual'
        events,_=await self.run_request({'a':429,'b':200})
        self.assertEqual(self.calls,['a']);self.assertEqual(events[0]['status'],429)

    def test_limits_expire_and_sunk_priority_recovers(self):
        self.pool.update('a',remaining=50,credits_updated=self.now)
        self.pool.record('a',429,model='m',retry_after=120)
        self.now+=121
        self.assertEqual(self.pool.state(self.rows[0],model='m'),'available')
        self.assertEqual(self.pool.select(model='m')[0],'b');self.pool.release('b')
        self.now+=1800
        self.pool.cursor=0
        self.assertEqual(self.pool.select(model='m')[0],'a');self.pool.release('a')

    def test_success_clears_sunk_but_not_other_model_limit(self):
        self.pool.update('a',remaining=50,credits_updated=self.now)
        self.pool.record('a',429,model='m',retry_after=120)
        self.pool.record('a',200,model='other')
        self.assertEqual(self.pool.state(self.rows[0],model='m'),'cooling')
        self.assertFalse(self.data['account_status']['a'].get('sunk_until'))

    def test_unknown_balance_is_not_assumed_exhausted(self):
        self.pool.update('a',remaining=None,credits_updated=self.now)
        self.pool.record('a',429,model='m')
        self.assertEqual(self.pool.state(self.rows[0]),'available')
        self.assertEqual(self.pool.state(self.rows[0],model='m'),'cooling')
        self.assertFalse(self.data['account_status']['a'].get('sunk_until'))

    def test_verified_reset_clock_is_not_shortened(self):
        from admin.pool import rate_delay
        self.assertEqual(rate_delay('120',self.now),120)
        self.assertEqual(rate_delay('invalid',self.now),60)
        self.assertGreater(rate_delay('120',self.now,'usage will reset at 2026-10-21 10:00:00 UTC+8'),120)

    def test_model_inventory_explains_limits_and_auto_recovers(self):
        from unified.adaptive import availability,model_inventory
        snapshot={'codebuddy':availability([{'id':'a','enabled':True,'remaining':50,
                  'model_cooldowns':{'m':self.now+120},'sunk_until':self.now+1800}])}
        limited=model_inventory(snapshot,'codebuddy/m',self.now)
        self.assertFalse(limited['available']);self.assertEqual(limited['candidates'][0]['cooldown_until'],self.now+120)
        self.assertTrue(model_inventory(snapshot,'codebuddy/other',self.now)['available'])
        self.assertTrue(model_inventory(snapshot,'codebuddy/m',self.now+121)['available'])


class WorkBuddyFailover(unittest.IsolatedAsyncioTestCase):
    async def run_request(self,statuses,stream=False,partial=False):
        from unified.workbuddy_login import WorkBuddyLogin
        self.calls=[]
        async def relay(row,path,body,scope,receive,send):
            aid=row['account_uid'];self.calls.append(aid)
            if partial:
                await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
                await send({'type':'http.response.body','body':b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n','more_body':True})
            if statuses[aid]==429:raise HTTPException(429,'upstream rate limit')
            blob=json.dumps({'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}).encode()
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':blob,'more_body':False})
        wb=WorkBuddyLogin(None,SimpleNamespace(relay_row=relay),'synthetic','synthetic')
        wb.accounts=AsyncMock(return_value=[{'uid':aid,'enabled':True,'realm':'intl','credits':{'remain':50}} for aid in statuses])
        api=SimpleNamespace(workbuddy=wb,network=None)
        scope={'type':'http','method':'POST','path':'/v1/chat/completions','headers':[], 'state':{}}
        async def receive():return {'type':'http.request','body':b'', 'more_body':False}
        events=[]
        async def send(e):events.append(e)
        trace=Trace('synthetic','synthetic','workbuddy/m');token=CURRENT_TRACE.set(trace)
        try:
            await dispatch(api,'workbuddy',scope,receive,send,json.dumps({'model':'m','stream':stream}).encode())
        finally:
            CURRENT_TRACE.reset(token)
            self.wb=wb;self.trace=trace;self.events=events

    async def test_switches_only_limited_accounts_and_keeps_attempt_chain(self):
        await self.run_request({'a':429,'b':429,'c':200})
        self.assertEqual(self.calls,['a','b','c'])
        self.assertEqual([a.get('account_id') for a in self.trace.attempts],['a','b','c'])
        self.assertEqual([a['status'] for a in self.trace.attempts],[429,429,200])
        self.assertFalse(any(self.wb.active.values()))
        self.assertGreater(self.wb.sunk_until['a'],0)
        ready,_=await self.wb.routing_accounts('other',set())
        self.assertEqual(len(ready),3)

    async def test_all_429s_preserve_real_status_without_repeating_accounts(self):
        with self.assertRaises(HTTPException) as ctx:await self.run_request({'a':429,'b':429})
        self.assertEqual(ctx.exception.status_code,429);self.assertEqual(self.calls,['a','b'])

    async def test_exception_after_output_never_switches_accounts(self):
        with self.assertRaises(HTTPException):await self.run_request({'a':429,'b':200},partial=True)
        self.assertEqual(self.calls,['a'])
        self.assertFalse(any(self.wb.active.values()))

    async def test_verified_window_survives_local_fallback(self):
        await self.run_request({'a':429,'b':200})
        import time
        now=int(time.time())
        live=SimpleNamespace(model_cooldowns={'m':now+86400},cooldown_until=0,credits={'remain':50})
        self.wb.runtime=SimpleNamespace(proxy=SimpleNamespace(POOL=SimpleNamespace(get=lambda aid:live)))
        with patch('unified.workbuddy_login.time.time',return_value=now):
            self.wb.record_route_limit('a','m')
            self.assertEqual(self.wb.route_limits['a']['m'],now+86400)
            self.assertEqual(self.wb.sunk_until['a'],now+1800)
