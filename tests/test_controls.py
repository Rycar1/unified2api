import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from unified.features import UnifiedConfig
from unified.key_controls import KeyControls
from unified.diagnostics import DiagnosticLog, ResponseObservation, Trace


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = SimpleNamespace(lock=threading.RLock(),data={'keys':{'key':{'name':'Synthetic'}}},save=lambda:None)
        self.config = UnifiedConfig(self.store)
        self.now = [1791907140.0]
        self.path = Path(self.temp.name)/'requests.sqlite'
        self.controls = KeyControls(self.config,self.path,clock=lambda:self.now[0])
        self.payload = {'model':'test/model','messages':[{'role':'user','content':'OK'}],'max_tokens':8}

    def tearDown(self):
        self.temp.cleanup()

    def test_daily_monthly_limits_and_atomic_reservations(self):
        reservation = self.controls.estimate(self.payload)
        self.controls.save('key',{'daily_tokens':2*reservation,'monthly_tokens':3*reservation})
        lease = self.controls.admit('key',self.payload,'one')
        self.controls.finish(lease,{'prompt_tokens':2*reservation-2,'completion_tokens':1})
        with self.assertRaises(HTTPException) as caught:
            self.controls.admit('key',self.payload,'two')
        self.assertEqual(caught.exception.status_code,429)
        self.assertEqual(self.controls.usage('key')['day_tokens'],2*reservation-1)
        self.now[0] += 86400
        lease = self.controls.admit('key',self.payload,'three')
        self.controls.finish(lease,{'prompt_tokens':reservation,'completion_tokens':1})
        with self.assertRaises(HTTPException):
            self.controls.admit('key',self.payload,'four')
        self.assertEqual(self.controls.usage('key')['month_tokens'],3*reservation)

    def test_pending_request_stays_visible_and_blocks_concurrency_across_month(self):
        from datetime import datetime
        from unified.key_controls import CN
        self.now[0] = datetime(2026,10,31,23,59,50,tzinfo=CN).timestamp()
        self.controls.save('key',{'max_concurrency':1})
        lease = self.controls.admit('key',self.payload,'cross-month')
        self.now[0] += 30
        self.assertEqual(self.controls.usage('key')['active'],1)
        with self.assertRaises(HTTPException):
            self.controls.admit('key',self.payload,'blocked')
        self.controls.finish(lease,{'prompt_tokens':1})
        self.assertEqual(self.controls.usage('key')['active'],0)

    def test_concurrent_budget_cannot_be_admitted_twice(self):
        required = self.controls.estimate(self.payload)
        self.controls.save('key',{'daily_tokens':required+1})
        lease = self.controls.admit('key',self.payload,'one')
        with self.assertRaises(HTTPException):
            self.controls.admit('key',self.payload,'two')
        self.controls.finish(lease,{},produced=False)
        self.assertIsNotNone(self.controls.admit('key',self.payload,'three'))

    def test_concurrency_release_rate_and_model_permissions(self):
        self.controls.save('key',{'max_concurrency':1,'requests_per_minute':2,'allowed_models':['test/*','route/approved']})
        lease = self.controls.admit('key',self.payload,'one')
        with self.assertRaises(HTTPException):
            self.controls.admit('key',self.payload,'two')
        self.controls.finish(lease,{})
        lease = self.controls.admit('key',self.payload,'three')
        self.controls.finish(lease,{})
        with self.assertRaises(HTTPException):
            self.controls.admit('key',self.payload,'four')
        self.now[0] += 61
        with self.assertRaises(HTTPException) as caught:
            self.controls.admit('key',{**self.payload,'model':'route/other'},'five')
        self.assertEqual(caught.exception.status_code,403)
        self.assertTrue(self.controls.allowed('key','route/approved'))

    def test_unknown_usage_and_process_recovery_are_conservative(self):
        self.controls.save('key',{'daily_tokens':10000})
        first = self.controls.admit('key',self.payload,'one')
        self.controls.finish(first,{},produced=True)
        pending = self.controls.admit('key',self.payload,'two')
        restored = KeyControls(self.config,self.path,clock=lambda:self.now[0])
        usage = restored.usage('key')
        self.assertEqual(usage['active'],0)
        self.assertEqual(usage['day_tokens'],2*restored.estimate(self.payload))
        self.assertEqual(usage['estimated_tokens'],usage['day_tokens'])

    def test_validation_rejects_unknown_fields_bad_types_and_deleted_keys(self):
        for body in ({'daily_tokens':True},{'max_concurrency':-1},{'allowed_models':'test/*'},
                     {'allowed_models':['test/model\nsecret']},{'monthly_tokens':1.5},{'unexpected':1}):
            with self.assertRaises(HTTPException):
                self.controls.save('key',body)
        with self.assertRaises(HTTPException):
            self.controls.save('missing',{'daily_tokens':1})

    def test_logging_does_not_store_content_and_clearing_logs_preserves_budget(self):
        trace = Trace('request-one','key','test/model')
        trace.attempts.append({'model':'test/model','account_id':'synthetic-id','status':200,
                               'request_body':'PRIVATE-PROMPT','reply':'PRIVATE-REPLY','key':'PRIVATE-TOKEN'})
        log = DiagnosticLog(self.path)
        log.record({'path':'/v1/chat/completions','model':'test/model','status':200,'ok':True,
                    'duration_ms':12,'outcome':'success','prompt_tokens':3},trace=trace)
        detail = log.detail('request-one')
        encoded = json.dumps(detail)
        self.assertNotIn('PRIVATE-',encoded)
        self.assertEqual(detail['attempts'][0]['account_id'],'synthetic-id')
        lease = self.controls.admit('key',self.payload,'two')
        self.controls.finish(lease,{'prompt_tokens':3,'completion_tokens':4})
        log.clear()
        self.assertEqual(self.controls.usage('key')['day_tokens'],7)
        self.assertEqual(log.query()['total'],0)

    def test_stream_observation_tracks_real_finish_and_usage_without_double_count(self):
        ticks = [0.0]
        observation = ResponseObservation('/v1/chat/completions',clock=lambda:ticks[0])
        observation.feed({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
        ticks[0] = .125
        observation.feed({'type':'http.response.body','body':b'data: {"choices":[{"delta":{"content":"synthetic"},"finish_reason":null}]}\n\n','more_body':True})
        observation.feed({'type':'http.response.body','body':b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":5,"completion_tokens_details":{"reasoning_tokens":2}}}\n\ndata: [DONE]\n\n','more_body':False})
        self.assertEqual(observation.finish()['outcome'],'success')
        self.assertEqual(observation.ttft_ms,125)
        self.assertEqual(observation.usage['completion_tokens'],5)
        self.assertEqual(observation.usage['reasoning_tokens'],2)

    def test_responses_and_messages_streams_need_protocol_completion(self):
        for path,body in [('/v1/responses',b'data: {"type":"response.completed","response":{"status":"completed"}}\n\n'),
                          ('/v1/messages',b'event: message_stop\ndata: {"type":"message_stop"}\n\n')]:
            observation = ResponseObservation(path)
            observation.feed({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
            observation.feed({'type':'http.response.body','body':body,'more_body':False})
            self.assertTrue(observation.finish()['ok'])
        observation = ResponseObservation('/v1/responses')
        observation.feed({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
        observation.feed({'type':'http.response.body','body':b'data: {"type":"response.created"}\n\n','more_body':False})
        self.assertEqual(observation.finish()['outcome'],'missing_completion')
