import asyncio
import json
import unittest
from types import SimpleNamespace

import httpx
from fastapi import HTTPException
from unified.zen_direct import ZenDirect


def sse(*frames):
    return ''.join('data: '+json.dumps(f)+'\n\n' for f in frames)+'data: [DONE]\n\n'


class ZenDirectTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime=SimpleNamespace(official_key='',outbound=None,token='synthetic-runtime-token')
        self.zen=ZenDirect(self.runtime)

    async def invoke(self,body,path='/v1/chat/completions',key='synthetic-key'):
        events=[]
        async def receive():await asyncio.Event().wait()
        async def send(event):events.append(event)
        await self.zen.relay('synthetic-service',path,json.dumps(body).encode(),
            {'type':'http','method':'POST','path':path,'asgi':{'spec_version':'2.0'},
             'headers':[(b'authorization',('Bearer '+key).encode())]},receive,send)
        data=b''.join(e.get('body',b'') for e in events)
        return events,data

    async def test_nonstream_uses_stream_upstream_and_preserves_prompt_and_model(self):
        def upstream(req):
            body=json.loads(req.content)
            self.assertTrue(body['stream'])
            self.assertEqual(body['model'],'glm-5.3-flash-free')
            self.assertEqual(body['messages'][0]['content'],'synthetic unchanged prompt')
            self.assertTrue(body['tools'])
            self.assertEqual(req.headers['authorization'],'Bearer public')
            self.assertEqual(req.headers['x-opencode-session'],req.headers['x-opencode-session-id'])
            self.assertRegex(req.headers['x-opencode-session'],r'^ses_[a-f0-9]{12}[A-Za-z0-9]{14}$')
            self.assertNotIn('synthetic-key',str(req.headers))
            return httpx.Response(200,text=sse({'model':body['model'],'choices':[{'delta':{'content':'synthetic answer'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]}))
        self.zen.transport=httpx.MockTransport(upstream)
        events,data=await self.invoke({'model':'glm-5.3-flash-free','stream':False,'messages':[{'role':'system','content':'synthetic unchanged prompt'}]})
        self.assertEqual(events[0]['status'],200)
        self.assertEqual(json.loads(data)['choices'][0]['message']['content'],'synthetic answer')

    async def test_custom_tool_survives_alongside_bridge_manifest(self):
        def upstream(req):
            body=json.loads(req.content)
            self.assertIn('synthetic_double',[t['function']['name'] for t in body['tools']])
            self.assertIn('read',[t['function']['name'] for t in body['tools']])
            return httpx.Response(200,text=sse({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'synthetic_call','function':{'name':'synthetic_double','arguments':'{"value":4}'}}]},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'tool_calls'}]}))
        self.zen.transport=httpx.MockTransport(upstream)
        _,data=await self.invoke({'model':'glm-5.3-flash-free','stream':False,'messages':[{'role':'user','content':'synthetic'}],
            'tools':[{'type':'function','function':{'name':'synthetic_double','parameters':{'type':'object'}}}]})
        self.assertEqual(json.loads(data)['choices'][0]['message']['tool_calls'][0]['function']['name'],'synthetic_double')

    async def test_bad_streams_fail_instead_of_faking_completion(self):
        for body in (sse({'choices':[{'delta':{'content':'unfinished'}}]}),
                     sse({'model':'different-model','choices':[{'delta':{'content':'wrong'},'finish_reason':'stop'}]}),
                     sse({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'x','function':{'name':'read','arguments':'{}'}}]},'finish_reason':'tool_calls'}]})):
            self.zen.transport=httpx.MockTransport(lambda req:httpx.Response(200,text=body))
            with self.assertRaises(HTTPException):
                await self.invoke({'model':'glm-5.3-flash-free','stream':False,'messages':[{'role':'user','content':'synthetic'}]})

    async def test_responses_continuation_isolated_by_api_key(self):
        def upstream(req):
            body=json.loads(req.content)
            self.assertTrue(body['messages'])
            return httpx.Response(200,text=sse({'choices':[{'delta':{'content':'synthetic'},'finish_reason':None}]},
                {'choices':[{'delta':{},'finish_reason':'stop'}]}))
        self.zen.transport=httpx.MockTransport(upstream)
        _,raw=await self.invoke({'model':'glm-5.3-flash-free','stream':False,'input':'synthetic first'},'/v1/responses')
        response=json.loads(raw)
        self.assertEqual(response['object'],'response')
        self.assertEqual(response['status'],'completed')
        follow={'model':'glm-5.3-flash-free','stream':False,'input':'synthetic second','previous_response_id':response['id']}
        await self.invoke(follow,'/v1/responses')
        with self.assertRaises(HTTPException):await self.invoke(follow,'/v1/responses',key='other-synthetic-key')
