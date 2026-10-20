import importlib
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch,AsyncMock

from unified.workbuddy_hooks import install
from unified.features import UnifiedConfig,AutomationManager


class HookTests(unittest.TestCase):
    def proxy(self,builder=None):
        class Pool:
            def get(self,uid): return SimpleNamespace(uid=uid,realm='intl',enabled=True,access_token='synthetic',
                ready=lambda model=None:model!='blocked',throttle_wait=lambda model=None:60 if model=='blocked' else 0,
                reserve_blocked=lambda:False,daily_limit_blocked=lambda:False,
                credit_limit_blocked=lambda model=None:False,model_token_limit_blocked=lambda model=None:False)
            def pick_for_session(self,realm='cn',exclude=None,session_id=None,model=None):
                return SimpleNamespace(uid='automatic',realm=realm)
        class Handler:
            def do_POST(self): return self.pool.pick_for_session(*self.args)
            def end_headers(self): pass
            def send_header(self,*args): pass
        proxy=SimpleNamespace(build_upstream_body=builder or (lambda p:p),record_error=lambda *a,**k:None,
            wb_webtools=SimpleNamespace(set_logger=lambda logger:None),Handler=Handler,
            wb_accounts=SimpleNamespace(AccountPool=Pool))
        install(proxy)
        return proxy

    def test_effort_alias_and_off_are_normalized_without_mutating_or_forcing_default(self):
        proxy=self.proxy()
        request={'model':'deepseek-v4.1-flash','messages':[{'role':'user','content':'synthetic'}]}
        self.assertNotIn('reasoning_effort',proxy.build_upstream_body(request))
        aliased={**request,'reasoningEffort':'off'}
        result=proxy.build_upstream_body(aliased)
        self.assertEqual(result['reasoning_effort'],'none')
        self.assertEqual(result['thinking']['type'],'disabled')
        self.assertNotIn('reasoning_effort',aliased)
        # Exercise an upstream builder that tries to insert a hard-coded default.
        injected=self.proxy(lambda body:{**body,'reasoning_effort':body.get('reasoning_effort','high')})
        self.assertNotIn('reasoning_effort',injected.build_upstream_body(request))

    def test_private_selection_respects_positional_exclusions_and_model_limits(self):
        proxy=self.proxy();handler=proxy.Handler();handler.pool=proxy.wb_accounts.AccountPool()
        handler.headers={'X-Unified-Account-Uid':'forced'}
        with patch.dict(os.environ,{'WORKBUDDY_EMBEDDED':'true'}):
            handler.args=('intl',set(),None,'ready')
            self.assertEqual(handler.do_POST().uid,'forced')
            handler.args=('intl',{'forced'},None,'ready')
            self.assertIsNone(handler.do_POST())
            handler.args=('intl',set(),None,'blocked')
            self.assertIsNone(handler.do_POST())
        handler.args=('intl',set(),None,'ready')
        with patch.dict(os.environ,{'WORKBUDDY_EMBEDDED':'false'}):
            self.assertEqual(handler.do_POST().uid,'automatic')

    def test_messages_cache_breakdown_keeps_total_prompt_consistent(self):
        source=str(Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub')
        if source not in sys.path: sys.path.insert(0,source)
        proxy=importlib.import_module('wb_proxy')
        usage=proxy._anthropic_usage({'prompt_tokens':100,'completion_tokens':3,'prompt_cache_hit_tokens':60,'prompt_cache_write_tokens':10})
        self.assertEqual(usage['input_tokens'],30)
        self.assertEqual(sum(usage[k] for k in ('input_tokens','cache_read_input_tokens','cache_creation_input_tokens')),100)


class AutomaticBenefits(unittest.IsolatedAsyncioTestCase):
    async def test_benefits_switch_is_opt_in_and_executes_the_correct_runner(self):
        data={}
        config=SimpleNamespace(get=lambda key,default=None:data.get(key,default),set=lambda key,value:data.update({key:value}))
        runner=AsyncMock(return_value={'providers':[],'summary':{'total':0,'succeeded':0,'failed':0}})
        automation=AutomationManager(config,runner)
        self.assertFalse(automation.settings().get('auto_benefits',False))
        automation.update({'auto_benefits':True})
        await automation.execute('benefits',manual=True)
        runner.assert_awaited_once_with('benefits')
