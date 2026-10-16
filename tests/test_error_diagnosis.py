import json
import unittest
from unified.error_diagnosis import classify_error, diagnosis
from unified.diagnostics import ResponseObservation


class ErrorDiagnosis(unittest.TestCase):
    def test_nested_provider_codes_are_classified_without_retaining_text(self):
        for code,category in [(11102,'model_not_found'),(11128,'channel_verification'),(11133,'parameters'),(6004,'quota_exhausted')]:
            raw={'error':{'message':'upstream 400: '+json.dumps({'code':code,'msg':'synthetic-private-text'})}}
            result=classify_error(raw,400)
            self.assertEqual(result,{'error_category':category,'upstream_code':str(code)})
            self.assertNotIn('synthetic-private',json.dumps(result))
            self.assertTrue(diagnosis(result)['suggestion'])

    def test_observation_stores_fixed_metadata_for_http_and_stream_errors(self):
        obs=ResponseObservation('/v1/chat/completions')
        obs.feed({'type':'http.response.start','status':400,'headers':[]})
        obs.feed({'type':'http.response.body','body':json.dumps({'code':11102,'msg':'secret-query'}).encode()})
        result=obs.finish()
        self.assertEqual(result['upstream_code'],'11102')
        self.assertEqual(result['error_category'],'model_not_found')
        self.assertNotIn('secret-query',str(result))
        self.assertEqual(classify_error({},429)['error_category'],'rate_limited')
        self.assertEqual(diagnosis({'status':200,'outcome':'missing_completion'})['category'],'stream_incomplete')

