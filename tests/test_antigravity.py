import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from unified.antigravity import AntigravityAccounts
from unified.antigravity_protocol import request_envelope, gemini_frames


class AntigravityPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict('os.environ', {'ANTIGRAVITY_CLIENT_ID':'synthetic-client-id','ANTIGRAVITY_CLIENT_SECRET':'synthetic-client-secret'})
        self.env.start()
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.invalid = False

        async def respond(req):
            self.calls.append(req)
            if req.url.host == 'oauth2.googleapis.com':
                await asyncio.sleep(0.01)
                if self.invalid:
                    return httpx.Response(400, json={'error':'invalid_grant', 'error_description':'synthetic-sensitive-token'})
                return httpx.Response(200, json={'access_token':'synthetic-access-secret', 'expires_in':3600})
            if req.url.path.endswith(':loadCodeAssist'):
                return httpx.Response(200, json={'cloudaicompanionProject':{'id':'synthetic-project'}})
            if req.url.path.endswith(':fetchAvailableModels'):
                return httpx.Response(200, json={'models':{'gemini-test':{'displayName':'Gemini test', 'maxTokens':1048576, 'supportsThinking':True}, 'claude-test':{}}})
            if req.url.path.endswith(':retrieveUserQuotaSummary'):
                return httpx.Response(200, json={'groups':[{'displayName':'Gemini', 'buckets':[{'remainingFraction':0.75, 'resetTime':'2026-10-17T00:00:00Z'}]}]})
            return httpx.Response(200, json={'email':'synthetic@example.test'})

        self.pool = AntigravityAccounts(SimpleNamespace(root=Path(self.temp.name)), transport=httpx.MockTransport(respond))

    async def asyncTearDown(self):
        await self.pool.close()
        self.temp.cleanup()
        self.env.stop()

    async def add(self, value='1//synthetic-refresh-secret'):
        return await self.pool.import_account({'credential':value, 'name':'Synthetic'})

    async def test_import_encrypts_credentials_and_publishes_only_safe_fields(self):
        account = await self.add('{"refresh_token":"1//synthetic-refresh-secret"}')
        self.assertEqual(account['provider'], 'antigravity')
        self.assertEqual(account['status'], 'ready')
        public = json.dumps(await self.pool.accounts())
        self.assertNotIn('synthetic-access-secret', public)
        self.assertNotIn('synthetic-refresh-secret', public)
        persisted = self.pool.path.read_bytes()
        self.assertNotIn(b'synthetic-refresh-secret', persisted)
        self.assertNotIn(b'synthetic-access-secret', persisted)
        other = AntigravityAccounts(self.pool.store, transport=self.pool.transport)
        self.assertEqual((await other.accounts())[0]['id'], account['id'])
        await other.close()

    async def test_refresh_is_single_flight_and_deleted_accounts_cannot_reappear(self):
        account = await self.add()
        row = self.pool.get(account['id'])
        row['expires_at'] = 0
        before = len(self.calls)
        tokens = await asyncio.gather(*(self.pool.ensure_token(row) for _ in range(8)))
        self.assertEqual(len(set(tokens)), 1)
        self.assertEqual(sum(r.url.host=='oauth2.googleapis.com' for r in self.calls[before:]), 1)
        self.pool.delete(account['id'])
        with self.assertRaises(HTTPException):
            await self.pool.ensure_token(row)
        self.assertEqual(await self.pool.accounts(), [])

    async def test_quota_percent_and_remote_gemini_catalog_are_kept_per_account(self):
        account = await self.add()
        value = await self.pool.operate(account['id'], 'balance')
        self.assertEqual(value['remaining'], 75)
        self.assertEqual(value['unit'], '%')
        self.assertEqual((await self.pool.accounts())[0]['remaining'], 75)
        models = await self.pool.models()
        self.assertEqual([r['id'] for r in models], ['antigravity/gemini-test'])
        self.assertEqual(models[0]['context_window'], 1048576)
        self.assertTrue(models[0]['reasoning'])

    async def test_invalid_grant_is_actionable_and_does_not_leak_tokens(self):
        account = await self.add()
        self.invalid = True
        with self.assertRaises(HTTPException) as result:
            await self.pool.operate(account['id'], 'refresh')
        self.assertEqual(result.exception.status_code, 401)
        self.assertNotIn('synthetic-sensitive-token', str(result.exception.detail))
        self.assertEqual((await self.pool.accounts())[0]['status'], 'invalid')

    async def test_bad_input_is_rejected_before_outbound_and_duplicate_import_updates(self):
        for credential in ('', 'a'*8193, {'base_url':'http://127.0.0.1', 'refresh_token':123}):
            with self.assertRaises(HTTPException):
                await self.pool.import_account({'credential':credential})
        self.assertEqual(self.calls, [])
        first = await self.add()
        second = await self.add()
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(await self.pool.accounts()), 1)

    async def test_missing_oauth_application_is_actionable_without_outbound(self):
        with patch.dict('os.environ', {'ANTIGRAVITY_CLIENT_ID':'','ANTIGRAVITY_CLIENT_SECRET':''}):
            with self.assertRaises(HTTPException) as result:
                await self.add()
        self.assertEqual(result.exception.status_code,503)
        self.assertEqual(self.calls,[])


class AntigravityProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_tools_images_and_thought_signatures_round_trip(self):
        payload = {'model':'gemini-3-flash', 'reasoning_effort':'high', 'max_tokens':16000,
            'messages':[{'role':'developer','content':'Synthetic rules'},
                {'role':'user','content':[{'type':'text','text':'Synthetic question'},
                  {'type':'image_url','image_url':{'url':'data:image/png;base64,c3ludGhldGlj'}}]},
                {'role':'assistant','tool_calls':[{'id':'call-1','type':'function','function':{'name':'lookup','arguments':'{"key":"value"}'},'extra_content':{'google':{'thought_signature':'synthetic-signature'}}}]},
                {'role':'tool','tool_call_id':'call-1','content':'{"answer":42}'}],
            'tools':[{'type':'function','function':{'name':'lookup','parameters':{'type':'object','properties':{'key':{'type':'string'}},'additionalProperties':False}}}],
            'tool_choice':{'type':'function','function':{'name':'lookup'}}}
        result = request_envelope(payload, 'synthetic-project')
        wire = result['request']
        self.assertEqual(result['project'], 'synthetic-project')
        self.assertEqual(wire['systemInstruction']['parts'][0]['text'], 'Synthetic rules')
        self.assertEqual(wire['contents'][0]['parts'][1]['inlineData']['mimeType'], 'image/png')
        self.assertEqual(wire['contents'][1]['parts'][0]['thoughtSignature'], 'synthetic-signature')
        self.assertEqual(wire['contents'][-1]['parts'][0]['functionResponse']['name'], 'lookup')
        self.assertEqual(wire['toolConfig']['functionCallingConfig']['allowedFunctionNames'], ['lookup'])
        self.assertNotIn('additionalProperties', wire['tools'][0]['functionDeclarations'][0]['parameters'])
        self.assertEqual(wire['generationConfig']['thinkingConfig']['thinkingBudget'], 10000)
        self.assertEqual(request_envelope(payload,'synthetic-project')['request']['sessionId'], wire['sessionId'])
        self.assertNotEqual(request_envelope(payload,'synthetic-project')['requestId'], result['requestId'])

    async def test_stream_reasoning_tools_usage_and_incomplete_eof(self):
        events = [
            {'response':{'candidates':[{'content':{'parts':[{'text':'Synthetic reasoning','thought':True}]}}]}},
            {'response':{'candidates':[{'content':{'parts':[{'functionCall':{'id':'call-1','name':'lookup','args':{'key':'value'}},'thoughtSignature':'synthetic-signature'}]},'finishReason':'STOP'}]}},
            {'response':{'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':3,'thoughtsTokenCount':2,'totalTokenCount':15}}}]
        async def lines(values):
            for value in values:
                yield 'data: '+json.dumps(value); yield ''
        frames = [r async for r in gemini_frames(lines(events))]
        choices = [c for f in frames for c in f.get('choices',[])]
        tool = next(c['delta']['tool_calls'][0] for c in choices if c['delta'].get('tool_calls'))
        self.assertEqual(tool['extra_content']['google']['thought_signature'], 'synthetic-signature')
        self.assertEqual(choices[-1]['finish_reason'], 'tool_calls')
        self.assertEqual(frames[-1]['usage']['completion_tokens'], 5)
        self.assertEqual(frames[-1]['usage']['completion_tokens_details']['reasoning_tokens'], 2)
        with self.assertRaises(HTTPException) as result:
            [r async for r in gemini_frames(lines(events[:1]))]
        self.assertEqual(result.exception.status_code, 502)

    async def test_remote_images_and_malformed_tool_arguments_fail_explicitly(self):
        for message in [
            {'role':'user','content':[{'type':'image_url','image_url':{'url':'http://127.0.0.1/private'}}]},
            {'role':'assistant','tool_calls':[{'id':'x','function':{'name':'f','arguments':'{invalid'}}]}]:
            with self.assertRaises(HTTPException):
                request_envelope({'model':'gemini-test','messages':[message]},'p')
