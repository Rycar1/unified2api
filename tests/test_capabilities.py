import asyncio
import threading
import unittest
from types import SimpleNamespace
from fastapi import HTTPException
from unified.features import UnifiedConfig
from unified.capabilities import ModelCapabilities


class Capabilities(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        store = SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None)
        self.caps = ModelCapabilities(UnifiedConfig(store))

    async def test_native_converted_and_unknown_are_distinct(self):
        self.assertEqual(self.caps.describe({'id':'qoder/test'})['protocols']['responses'],'converted')
        self.assertEqual(self.caps.describe({'id':'trae/test'})['protocols']['responses'],'unsupported')
        custom = self.caps.describe({'id':'custom/test'})
        self.assertEqual(custom['protocols']['responses'],'unknown')
        self.assertIsNone(custom['vision'])
        self.assertIsNone(custom['tools'])
        self.assertIsNone(custom['context_window'])
        self.assertIsNone(custom['compact'])

    async def test_route_capabilities_follow_usable_targets_and_cannot_be_forged(self):
        rows = {name:self.caps.describe({'id':name}) for name in ['trae/test','qoder/test']}
        value = self.caps.route({'id':'route/mixed'},list(rows),rows)
        self.assertEqual(value['protocols']['responses'],'converted')
        self.assertEqual(value['protocols']['chat'],'supported')
        self.assertFalse(value['compact'])
        with self.assertRaises(HTTPException):
            self.caps.save('route/mixed',{'compact':True})

    async def test_explicit_metadata_and_manual_overrides_have_provenance(self):
        row = {'id':'qoder/test','vision':True,'context_window':32768,'catalog_source':'live'}
        cap = self.caps.describe(row)
        self.assertTrue(cap['vision'])
        self.assertEqual(cap['context_window'],32768)
        self.caps.save(row['id'],{'tools':True,'context_window':64000})
        cap = self.caps.describe(row)
        self.assertTrue(cap['tools'])
        self.assertEqual(cap['sources']['tools'],'manual')
        self.assertEqual(cap['context_window'],64000)
        with self.assertRaises(HTTPException):
            self.caps.save(row['id'],{'context_window':True})

    async def test_probe_requires_actual_tool_call_and_compaction_item(self):
        async def runner(path,body):
            if path.endswith('/compact'):
                return 200,{'output':[{'type':'message','content':[]}]}
            if path.endswith('/responses'):
                return 200,{'object':'response','status':'completed','output':[]}
            return 200,{'choices':[{'message':{'role':'assistant','content':'I can use tools'}}]}
        result = await self.caps.probe({'id':'custom/test'},runner)
        self.assertIsNone(result['tools'])
        self.assertEqual(result['protocols']['responses'],'native')
        self.assertFalse(result['compact'])
        self.assertIsNone(result['vision'])

    async def test_tool_probe_checks_name_and_arguments(self):
        async def runner(path,body):
            if path.endswith('/chat/completions'):
                import json
                return 200,{'choices':[{'message':{'tool_calls':[{'type':'function','function':{'name':'unified_probe','arguments':json.dumps({'value':body['messages'][-1]['content']})}}]}}]}
            if path.endswith('/compact'):
                return 200,{'output':[{'type':'compaction','encrypted_content':'opaque-synthetic-data'}]}
            return 200,{'object':'response','status':'completed','output':[]}
        result = await self.caps.probe({'id':'custom/test'},runner)
        self.assertTrue(result['tools'])
        self.assertTrue(result['compact'])
