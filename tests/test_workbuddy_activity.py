import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unified.workbuddy_activity import WebActivity


class WebActivityTests(unittest.TestCase):
    def setUp(self):
        guard=patch('unified.workbuddy_activity.guard_sandbox')
        guard.start(); self.addCleanup(guard.stop)
        self.tmp=tempfile.TemporaryDirectory()
        self.account=SimpleNamespace(uid='synthetic',realm='intl',access_token='secret-not-for-output',proxy='',
            credits={'remain':100},fetch_credits=lambda: {'ok':True})
        self.config=SimpleNamespace(get=lambda k,d=None:d,set=lambda k,v:None)
        self.activity=WebActivity(self.config,runner=lambda *a,**k:{'ok':True,'status':'completed','chunks':2,'elapsed_ms':10})

    def tearDown(self): self.tmp.cleanup()

    def test_session_is_driven_and_no_sandbox_token_is_returned(self):
        calls=[]
        def request(account,path,body=None):
            calls.append(path)
            if path=='': return {'code':0,'data':{'id':'new-synthetic-conversation'}}
            if path.endswith('/session'): return {'data':{'link':'https://sandbox.example/acp','token':'sandbox-secret','sessionId':'session'}}
            return {'data':{'status':'completed'}}
        self.activity.request=request
        result=self.activity.run(self.account)
        self.assertTrue(result['ok'])
        self.assertEqual(result['chunks'],2)
        self.assertNotIn('sandbox-secret',str(result))
        self.assertEqual(calls,['','new-synthetic-conversation/session'])

    def test_failed_flow_is_not_recorded_as_success(self):
        state={}
        self.config.get=lambda k,d=None:state.get(k,d)
        self.config.set=lambda k,v:state.update({k:v})
        self.activity.request=lambda *a,**k:{'data':{'id':'new'}}
        result=self.activity.run(self.account)
        self.assertFalse(result['ok'])
        self.assertNotIn('web_activity',state)

    def test_same_day_completed_flow_is_idempotent(self):
        state={}
        self.config.get=lambda k,d=None:state.get(k,d)
        self.config.set=lambda k,v:state.update({k:v})
        self.activity.request=lambda a,p,b=None: {'data':{'id':'new'}} if p=='' else {'data':{'link':'https://sandbox.example/acp','token':'secret'}}
        first=self.activity.run(self.account)
        second=self.activity.run(self.account)
        self.assertTrue(first['ok'])
        self.assertTrue(second['already'])
        self.assertEqual(len(state['activity_history']),1)
        self.assertEqual(state['activity_history'][0]['action'],'daily_activity')
        self.assertNotIn('secret',str(state['activity_history']))

    def test_completed_session_is_not_repeated_when_balance_refresh_fails(self):
        state={}
        self.config.get=lambda k,d=None:state.get(k,d)
        self.config.set=lambda k,v:state.update({k:v})
        self.activity.request=lambda a,p,b=None: {'data':{'id':'new'}} if p=='' else {'data':{'link':'https://sandbox.example/acp','token':'secret'}}
        def fail(): raise OSError('refresh temporarily failed')
        self.account.fetch_credits=fail
        first=self.activity.run(self.account)
        self.assertTrue(first['ok']); self.assertTrue(first['balance_stale'])
        self.assertTrue(self.activity.run(self.account)['already'])

    def test_scheduler_trigger_argument_uses_completed_web_activity(self):
        class Account:
            def daily_chat(self,web=None,trigger='unknown'): return {'ok':False}
        proxy=SimpleNamespace()
        self.activity.run=lambda account:{'ok':True,'message':'completed synthetic activity'}
        with patch.dict('sys.modules',{'wb_accounts':SimpleNamespace(Account=Account)}):
            self.activity.install(proxy)
            account=Account();account.realm='intl'
            result=account.daily_chat(trigger='scheduler')
            self.assertTrue(result['ok'])
            self.assertIn('completed',result['msg'])
