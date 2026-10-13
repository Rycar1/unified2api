import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unified.diagnostics import DiagnosticLog,Trace,ResponseObservation
from unified.performance import performance_summary,price_attempts


class Performance(unittest.TestCase):
    def test_cached_tokens_credit_and_generation_rate_are_recorded_without_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=DiagnosticLog(Path(tmp)/'usage.sqlite')
            trace=Trace('synthetic-request','key','workbuddy/deepseek-v4.1-flash')
            trace.response.status=200; trace.response.completed=True; trace.response.ttft_ms=100
            trace.response.value({'choices':[{'message':{'content':'PRIVATE-MUST-NOT-BE-PERSISTED'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':100,'completion_tokens':50,'prompt_tokens_details':{'cached_tokens':60},'credit':0.25}})
            log.record({'path':'/v1/chat/completions','model':trace.model,'resolved_model':trace.model,'status':200,
                'ok':True,'duration_ms':1100,'outcome':'success'},trace)
            row=log.detail('synthetic-request')
            self.assertEqual(row['cached_tokens'],60)
            self.assertEqual(row['credits'],0.25)
            self.assertAlmostEqual(row['tokens_per_second'],50,places=2)
            summary=performance_summary(log,30)
            self.assertEqual(summary['known_cache_requests'],1)
            self.assertEqual(summary['cached_tokens'],60)
            self.assertEqual(len(summary['series']),1)
            self.assertEqual(summary['series'][0]['cached_tokens'],60)
            self.assertNotIn('PRIVATE-MUST-NOT-BE-PERSISTED',Path(tmp,'usage.sqlite').read_bytes().decode('latin1'))

    def test_historic_unknown_cache_and_price_are_not_reported_as_known_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            log=DiagnosticLog(Path(tmp)/'usage.sqlite')
            log.record({'path':'/v1/chat/completions','model':'custom/unknown','status':200,'ok':True,
                'duration_ms':100,'outcome':'success','prompt_tokens':5,'completion_tokens':10})
            summary=performance_summary(log,30)
            self.assertEqual(summary['known_cache_requests'],0)
            self.assertIsNone(summary['cache_hit_percent'])
            self.assertEqual(summary['priced_requests'],0)
            self.assertIsNone(summary['estimated_cost_cny'])

    def test_anthropic_partial_usage_preserves_input_counts(self):
        observation=ResponseObservation('/v1/messages')
        observation.value({'type':'message_start','message':{'usage':{'input_tokens':100,'cache_read_input_tokens':60}}})
        observation.value({'type':'message_delta','usage':{'output_tokens':30}})
        self.assertEqual(observation.usage['prompt_tokens'],160)
        self.assertEqual(observation.usage['completion_tokens'],30)
        self.assertEqual(observation.usage['cached_tokens'],60)

    def test_failover_cost_uses_each_attempts_model(self):
        attempts=[{'kind':'provider','model':'a/one','prompt_tokens':10},
                  {'kind':'provider','model':'b/two','prompt_tokens':5}]
        with patch('unified.performance.price_usage',side_effect=[{'cny':1},{'cny':2}]):
            self.assertEqual(price_attempts(attempts)['cny'],3)
        with patch('unified.performance.price_usage',side_effect=[{'cny':1},{}]):
            self.assertNotIn('cny',price_attempts(attempts))
