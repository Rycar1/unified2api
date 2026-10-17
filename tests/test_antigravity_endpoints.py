import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from unified.antigravity import AntigravityAccounts, parse_models


class AntigravityEndpoints(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.pool = AntigravityAccounts(SimpleNamespace(root=Path(self.temp.name)))
        self.row = {'id':'synthetic', 'name':'Synthetic', 'enabled':True,
                    'access_token':'synthetic-access', 'refresh_token':'1//synthetic-refresh',
                    'project_id':'synthetic-project', 'expires_at':time.time()+3600}
        self.pool.rows = [self.row]
        self.calls = []

    def respond(self, statuses):
        def response(request):
            self.calls.append(request)
            code = statuses.get(request.url.host, 404)
            if code != 200:
                return httpx.Response(code, json={'error':{'message':'Resource has been exhausted.' if code==429 else 'Requested entity was not found.'}})
            event={'response':{'candidates':[{'content':{'parts':[{'text':'Synthetic reply'}]},'finishReason':'STOP'}]}}
            return httpx.Response(200, headers={'Content-Type':'text/event-stream'},text='data: '+json.dumps(event)+'\n\n')
        self.pool.transport = httpx.MockTransport(response)

    async def generate(self):
        return [f async for f in self.pool.chat_chunks({'model':'gemini-3.6-flash-high',
            'messages':[{'role':'user','content':'Synthetic prompt'}], 'max_tokens':32})]

    async def test_model_is_preserved_when_daily_is_missing_and_prod_works(self):
        self.respond({'cloudcode-pa.googleapis.com':200})
        frames = await self.generate()
        self.assertTrue(any(c.get('finish_reason') for f in frames for c in f.get('choices',[])))
        self.assertEqual([r.url.host for r in self.calls],[
            'daily-cloudcode-pa.googleapis.com','daily-cloudcode-pa.sandbox.googleapis.com','cloudcode-pa.googleapis.com'])
        self.assertTrue(all(json.loads(r.content)['model']=='gemini-3.6-flash-high' for r in self.calls))

    async def test_capacity_limit_is_not_hidden_by_another_endpoints_404(self):
        self.respond({'cloudcode-pa.googleapis.com':429})
        with self.assertRaises(HTTPException) as result:
            await self.generate()
        self.assertEqual(result.exception.status_code,429)
        self.assertIn('gemini-3.6-flash-high',str(result.exception.detail))
        self.assertIn('prod 429',str(result.exception.detail))
        self.assertNotIn('synthetic-access',str(result.exception.detail))

    async def test_all_missing_models_return_actionable_error(self):
        self.respond({})
        with self.assertRaises(HTTPException) as result: await self.generate()
        self.assertEqual(result.exception.status_code,404)
        self.assertEqual(len(self.calls),3)
        self.assertIn('gemini-3.6-flash-high',str(result.exception.detail))
        self.assertIn('入口',str(result.exception.detail))

    async def test_successful_stream_does_not_probe_extra_endpoints(self):
        self.respond({'daily-cloudcode-pa.googleapis.com':200})
        await self.generate()
        self.assertEqual(len(self.calls),1)

    async def test_auth_failure_is_not_retried_across_hosts(self):
        self.respond({'daily-cloudcode-pa.googleapis.com':403})
        with self.assertRaises(HTTPException) as result: await self.generate()
        self.assertEqual(result.exception.status_code,403)
        self.assertEqual(len(self.calls),1)

    async def test_initial_quota_error_keeps_account_rotation_policy(self):
        self.respond({'daily-cloudcode-pa.googleapis.com':429})
        with self.assertRaises(HTTPException) as result: await self.generate()
        self.assertEqual(result.exception.status_code,429)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(HTTPException) as cooling: await self.generate()
        self.assertEqual(cooling.exception.status_code,429)
        self.assertIn('冷却',str(cooling.exception.detail))
        self.assertEqual(len(self.calls),1)

    async def test_output_then_disconnect_is_not_replayed_on_another_host(self):
        class Interrupted(httpx.AsyncByteStream):
            async def __aiter__(self):
                event={'response':{'candidates':[{'content':{'parts':[{'text':'Partial synthetic reply'}]}}]}}
                yield ('data: '+json.dumps(event)+'\n\n').encode()
                raise httpx.ReadError('Synthetic disconnect')
        def respond(request):
            self.calls.append(request)
            return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Interrupted())
        self.pool.transport=httpx.MockTransport(respond)
        frames=[]
        with self.assertRaises(HTTPException):
            async for frame in self.pool.chat_chunks({'model':'gemini-3.6-flash-high',
                    'messages':[{'role':'user','content':'Synthetic prompt'}]}):
                frames.append(frame)
        self.assertTrue(frames)
        self.assertEqual(len(self.calls),1)

    async def test_model_catalog_prioritizes_actual_upstream_default(self):
        models=parse_models({'models':{'gemini-3.6-flash-high':{},'gemini-3.8-flash-high':{}},
            'defaultAgentModelId':'gemini-3.8-flash-high'})
        self.assertEqual(models[0]['id'],'antigravity/gemini-3.8-flash-high')
