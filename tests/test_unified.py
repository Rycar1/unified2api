import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app, API
from unified.trae import Trae


class Integration(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ.update(UNIFIED_API_KEY="test-public", TW2A_API_KEY="test-public",
            TW2A_AUTH_DIR=cls.tmp.name+"/trae", TW2A_STATE_FILE=cls.tmp.name+"/state.json")
        cls.native = Trae()

    @classmethod
    def tearDownClass(cls):
        cls.native.close()
        cls.tmp.cleanup()

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.buddy = create_buddy(root=self.temp.name+"/management", auth_dir=self.temp.name+"/auth",
            initial_key="test-public", admin_key="long-admin-key-for-unified-test", secure_cookie=False)
        self.app = create_app(native=self.native, buddy=self.buddy)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")
        login = await self.client.post("/admin/api/login", json={"key":"long-admin-key-for-unified-test"})
        self.assertEqual(login.status_code, 200)
        self.csrf = {"X-CSRF-Token": login.json()["csrf"]}

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def test_auth_keys_and_models(self):
        self.assertEqual((await self.client.get("/v1/models")).status_code, 401)
        res = await self.client.get("/v1/models", headers={"Authorization":"Bearer test-public"})
        ids = [m["id"] for m in res.json()["data"]]
        self.assertTrue(any(m.startswith("trae/") for m in ids))
        self.assertTrue(any(m.startswith("codebuddy/") for m in ids))
        denied = await self.client.post("/admin/api/keys", json={"name":"denied"})
        self.assertEqual(denied.status_code, 403)
        key = (await self.client.post("/admin/api/keys", json={"name":"both"}, headers=self.csrf)).json()
        headers = {"Authorization":"Bearer "+key["key"]}
        self.assertEqual((await self.client.get("/v1/models",headers=headers)).status_code, 200)
        await self.client.delete("/admin/api/keys/"+key["id"], headers=self.csrf)
        self.assertEqual((await self.client.get("/v1/models",headers=headers)).status_code, 401)

    async def test_dual_accounts_import_update_delete(self):
        doc = {"account":{"uid":"test-unified-account", "nickname":"test", "enterpriseId":"ent"},
               "auth":{"accessToken":"fake-access", "refreshToken":"fake-refresh", "expiresAt":2000000000000,
                       "machineId":"abcdef0123456789abcdef0123456789", "deviceId":"1234567890123456"}}
        t = await self.client.post("/admin/api/unified/trae/accounts",json={"credential":doc,"name":"TRAE test"},headers=self.csrf)
        self.assertEqual(t.status_code,200,t.text)
        b = await self.client.post("/admin/api/accounts",json={"credential":doc,"name":"Buddy test"},headers=self.csrf)
        self.assertEqual(b.status_code,200,b.text)
        rows=(await self.client.get("/admin/api/unified/overview")).json()["accounts"]
        self.assertEqual({a["provider"] for a in rows},{"trae","codebuddy"})
        self.assertTrue(all(a["expires_at"] == 2000000000000 for a in rows))
        self.assertNotIn("fake-access", json.dumps(rows))
        tpath="/admin/api/unified/trae/accounts/test-unified-account"
        bpath="/admin/api/accounts/"+b.json()["id"]
        for path in (tpath,bpath):
            self.assertEqual((await self.client.patch(path,json={"enabled":False,"name":"renamed"},headers=self.csrf)).status_code,200)
        rows=(await self.client.get("/admin/api/unified/overview")).json()["accounts"]
        self.assertTrue(all(not a["enabled"] and a["name"]=="renamed" for a in rows))
        for path in (tpath,bpath):
            self.assertEqual((await self.client.delete(path,headers=self.csrf)).status_code,200)

    async def test_validation_no_account_and_session(self):
        for model,path,expected in [("plain","chat/completions",400),("unknown/x","chat/completions",400),("trae/x","responses",400),("trae/glm-5.2","chat/completions",503),("codebuddy/glm-5.2","chat/completions",503)]:
            res=await self.client.post("/v1/"+path,headers={"Authorization":"Bearer test-public"},json={"model":model,"messages":[{"role":"user","content":"hi"}]})
            self.assertEqual(res.status_code,expected,res.text)
        result=await self.client.post("/admin/api/unified/test",headers=self.csrf,json={"model":"trae/x","message":"hi"})
        self.assertFalse(result.json()["ok"])
        self.assertEqual(len(self.buddy.state.store.test_keys),0)
        await self.client.post("/admin/api/logout",headers=self.csrf)
        self.assertEqual((await self.client.get("/admin/api/unified/overview")).status_code,401)

    async def test_trae_login_owner_and_cancel(self):
        res=await self.client.post("/admin/api/unified/trae/login",headers=self.csrf,json={})
        self.assertEqual(res.status_code,200,res.text)
        data=res.json()
        self.assertTrue(data["login_url"].startswith("https://"))
        self.assertIn(":18080/authorize",data["callback_url"])
        path="/admin/api/unified/trae/login/"+data["pending_id"]
        self.assertEqual((await self.client.get(path)).json()["state"],"pending")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url="http://localhost") as other:
            login=await other.post("/admin/api/login",json={"key":"long-admin-key-for-unified-test"})
            self.assertEqual(login.status_code,200)
            self.assertEqual((await other.get(path)).status_code,404)
        self.assertEqual((await self.client.delete(path,headers=self.csrf)).status_code,200)


class Streaming(unittest.IsolatedAsyncioTestCase):
    async def test_empty_tool_calls_are_removed_but_real_calls_remain(self):
        from core.converter import _stream_upstream
        import core.converter as converter
        class Response:
            status_code = 200
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
            async def aiter_bytes(self):
                yield b'data: {"choices":[{"delta":{"reasoning_content":"The","tool_calls":[]}}]}\n\n'
                yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"shell"}}]}}]}\n\n'
        class Stream:
            def __init__(self,*args,**kwargs): pass
            async def __aenter__(self): return Response()
            async def __aexit__(self,*args): pass
        class Client:
            def __init__(self,*args,**kwargs): pass
            def stream(self,*args,**kwargs): return Stream()
            async def __aenter__(self): return self
            async def __aexit__(self,*args): pass
        old = converter.httpx.AsyncClient
        converter.httpx.AsyncClient = Client
        try:
            chunks=[]
            async for chunk in _stream_upstream("http://test", {}, {"stream":True}): chunks.append(chunk)
        finally:
            converter.httpx.AsyncClient = old
        output=b"".join(chunks)
        self.assertIn(b'"reasoning_content":"The"', output)
        self.assertNotIn(b'"tool_calls":[]', output)
        self.assertIn(b'"tool_calls":[{"index":0', output)

    async def test_delivery_and_disconnect_cleanup(self):
        closed=asyncio.Event()
        class Native:
            async def events(self,*args):
                try:
                    yield {"status":200,"headers":{"Content-Type":["text/event-stream"]}}
                    yield {"data":base64.b64encode(b"data: first\n\n").decode()}
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        api=API(None,None,Native())
        receive_queue=asyncio.Queue()
        sent=[]
        async def send(event):
            sent.append(event)
            if event.get("body")==b"data: first\n\n":
                await receive_queue.put({"type":"http.disconnect"})
        await asyncio.wait_for(api.stream_native({"method":"POST","path":"/v1/chat/completions"},receive_queue.get,send,b"{}"),2)
        self.assertTrue(closed.is_set())
        self.assertTrue(any(e.get("body")==b"data: first\n\n" for e in sent))

    async def test_codebuddy_routing_preserves_payload(self):
        received={}
        class Store:
            def check_api(self,*args): pass
        class State:
            store=Store()
        class Buddy:
            state=State()
            async def __call__(self,scope,receive,send):
                received.update(json.loads((await receive())["body"]))
                await send({"type":"http.response.start","status":200,"headers":[]})
                await send({"type":"http.response.body","body":b"ok"})
        api=API(None,Buddy(),None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="http://test") as client:
            res=await client.post("/v1/responses",json={"model":"codebuddy/test","big":9007199254740993,"tools":[{"type":"function"}]})
        self.assertEqual(res.status_code,200)
        self.assertEqual(received["model"],"test")
        self.assertEqual(received["big"],9007199254740993)
        self.assertEqual(received["tools"],[{"type":"function"}])


if __name__=="__main__": unittest.main()
