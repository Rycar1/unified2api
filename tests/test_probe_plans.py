import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from fastapi import HTTPException
from unified.features import UnifiedConfig
from unified.probe_plans import ProbePlans


class ProbePlansTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=UnifiedConfig(SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None))
        self.now=1792140000
        self.calls=[]
        async def runner(path,body):
            self.calls.append((path,body))
            marker=body.get('messages',[{'content':body.get('input')}])[-1]['content']
            if 'tools' in body:
                value={'choices':[{'message':{'tool_calls':[{'function':{'name':'unified_probe','arguments':json.dumps({'value':marker})}}]},'finish_reason':'tool_calls'}]}
            else: value={'choices':[{'message':{'content':marker},'finish_reason':'stop'}]}
            return {'status':200,'value':value,'observation':{'ok':True,'finish_reason':'stop','completed':True,'stream':body.get('stream',False),'produced':True}}
        self.runner=runner
        self.path=Path(self.temp.name)/'probes.sqlite'
        self.probes=ProbePlans(self.config,self.path,runner,clock=lambda:self.now)

    def tearDown(self): self.temp.cleanup()

    async def test_budget_is_durable_and_content_is_never_saved(self):
        self.probes.update({'models':['custom/m'],'checks':['chat','tools'],'daily_requests':1,'daily_tokens':10000})
        await self.probes.run()
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.probes.status()['usage']['requests'],1)
        restarted=ProbePlans(self.config,self.path,self.runner,clock=lambda:self.now)
        await restarted.run()
        self.assertEqual(len(self.calls),1)
        self.assertNotIn('unified-probe-',json.dumps(restarted.status()))

    async def test_schedules_are_opt_in_and_deduplicate_one_interval(self):
        await self.probes.tick()
        self.assertEqual(self.calls,[])
        self.probes.update({'enabled':True,'models':['custom/m'],'checks':['stream'],'interval_minutes':30})
        await self.probes.tick(); await self.probes.tick()
        self.assertEqual(len(self.calls),1)
        self.now+=1801
        await self.probes.tick()
        self.assertEqual(len(self.calls),2)

    async def test_tools_require_real_call_and_timeout_releases_running_lease(self):
        async def no_tool(path,body):
            return {'status':200,'value':{'choices':[{'message':{'content':'I used a tool'}}]},'observation':{'ok':True}}
        self.probes.runner=no_tool
        self.probes.update({'models':['custom/m'],'checks':['tools']})
        await self.probes.run()
        self.assertFalse(self.probes.status()['history'][0]['ok'])
        self.assertEqual(self.probes.status()['history'][0]['outcome'],'capability_unverified')
        for body in ({'checks':['unknown']},{'daily_requests':0},{'interval_minutes':1}):
            with self.assertRaises(HTTPException): self.probes.update(body)

