import unittest
import threading
from types import SimpleNamespace
from unified.model_metadata import enrich_model
from unified.capabilities import ModelCapabilities
from unified.features import UnifiedConfig


class ModelMetadata(unittest.TestCase):
    def test_catalog_default_context_and_real_choices_are_preserved(self):
        row={'id':'qoder/test','context_config':{'200k':{'token_count':200000,'is_default':True},'1m':{'token_count':1000000}},
             'thinking_config':{'enabled':{'efforts':{'low':{'is_default':True},'high':{}}},'disabled':{}},
             'description':'Official description','price_factor':1.2,'enable':False,'disabled_reason':'Plan required'}
        result=enrich_model(row)
        self.assertEqual(result['context_window'],200000)
        self.assertEqual(result['context_options'],[200000,1000000])
        self.assertEqual(result['reasoning_efforts'],['low','high'])
        self.assertEqual(result['default_reasoning_effort'],'low')
        self.assertFalse(result['available'])
        self.assertNotIn('max_output_tokens',result)
        self.assertEqual(result['disabled_reason'],'Plan required')

    def test_official_specs_do_not_override_channel_limits(self):
        result=enrich_model({'id':'codebuddy/deepseek-v4.1-flash','context_window':262144,'max_output_tokens':8192})
        self.assertEqual(result['context_window'],262144)
        self.assertEqual(result['max_output_tokens'],8192)
        self.assertEqual(result['official_limits']['context_window'],1048576)
        self.assertEqual(result['official_limits']['max_output_tokens'],393216)

    def test_verified_official_defaults_have_source_and_unknowns_stay_unknown(self):
        result=enrich_model({'id':'workbuddy/glm-5.3-flash'})
        self.assertEqual(result['context_window'],1048576)
        self.assertEqual(result['max_output_tokens'],131072)
        self.assertEqual(result['limits_source'],'official-model')
        self.assertEqual(result['default_reasoning_effort'],'max')
        self.assertIn('https://docs.z.ai/',result['spec_source'])
        unknown=enrich_model({'id':'qoder/unpublished'})
        self.assertNotIn('context_window',unknown)
        self.assertNotIn('max_output_tokens',unknown)

    def test_route_limits_use_smallest_known_channel_and_allow_manual_output(self):
        store=SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None)
        caps=ModelCapabilities(UnifiedConfig(store))
        rows={'a/one':caps.describe({'id':'a/one','context_window':200000,'max_output_tokens':8192}),
              'b/one':caps.describe({'id':'b/one','context_window':1000000,'max_output_tokens':131072})}
        result=caps.route({'id':'route/test'},list(rows),rows)
        self.assertEqual(result['context_window'],200000)
        self.assertEqual(result['max_output_tokens'],8192)
        caps.save('a/one',{'max_output_tokens':16384})
        self.assertEqual(caps.describe({'id':'a/one'})['max_output_tokens'],16384)
