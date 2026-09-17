import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app
from unified.custom import Connections


class Native:
    async def request(self, method, path, body=b""):
        return 200, {"accounts": [], "data": [{"id": "model"}]}

    async def events(self, method, path, body=b""):
        self.last = (path, json.loads(body))
        yield {"status": 200, "headers": {"Content-Type": ["application/json"]}}
        yield {"data": base64.b64encode(b'{"choices":[]}').decode()}


class ConnectionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.native = Native()
        buddy = create_buddy(root=self.temp.name+"/management", auth_dir=self.temp.name+"/auth",
                             initial_key="client-key", admin_key="long-admin-key-for-tests", secure_cookie=False)
        self.app = create_app(native=self.native, buddy=buddy)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        login = await self.client.post("/admin/api/login", json={"key": "long-admin-key-for-tests"})
        self.csrf = {"X-CSRF-Token": login.json()["csrf"]}
        self.api_headers = {"Authorization": "Bearer client-key", "Cookie": "private=admin-cookie", "X-Api-Key": "client-key"}
        self.body = {"id": "myapi", "name": "测试服务", "base_url": "https://example.test", "key": "private-upstream-key", "models": ["org/model"]}
        self.path = "/admin/api/unified/connections"

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def add(self):
        response = await self.client.post(self.path, headers=self.csrf, json=self.body)
        self.assertEqual(response.status_code, 200, response.text)

    async def test_crud_persistence_validation_and_csrf(self):
        self.assertEqual((await self.client.post(self.path, json=self.body)).status_code, 403)
        for changes in ({"id": "trae"}, {"base_url": "file:///tmp"}, {"base_url": "https://user:password@host/v1"}, {"key": "bad\nkey"}, {"enabled": "false"}, {"models": "x"}):
            self.assertEqual((await self.client.post(self.path, headers=self.csrf, json=self.body | changes)).status_code, 400)
        await self.add()
        self.assertEqual((await self.client.post(self.path, headers=self.csrf, json=self.body)).status_code, 409)
        overview = await self.client.get("/admin/api/unified/overview")
        self.assertNotIn(self.body["key"], overview.text)
        self.assertEqual(overview.json()["connections"][0]["base_url"], "https://example.test/v1")
        await self.client.patch(self.path+"/myapi", headers=self.csrf, json={"name": "new", "key": ""})
        loaded = Connections(self.app.state.connections.path)
        self.assertEqual(loaded.get("myapi")["key"], self.body["key"])
        self.assertEqual(loaded.get("myapi")["name"], "new")
        self.assertEqual((await self.client.delete(self.path+"/myapi", headers=self.csrf)).status_code, 200)
        self.assertEqual(Connections(loaded.path).rows(), [])

    async def test_discovery_routing_headers_and_disable(self):
        async def upstream(request):
            self.assertEqual(request.headers["authorization"], "Bearer private-upstream-key")
            self.assertNotIn("cookie", request.headers)
            self.assertNotIn("x-api-key", request.headers)
            if request.method == "GET":
                self.assertEqual(str(request.url), "https://example.test/v1/models")
                return httpx.Response(200, json={"data": [{"id": "org/model"}]})
            self.assertEqual(json.loads(request.content)["model"], "org/model")
            self.assertIn(request.url.path, {"/v1/chat/completions", "/v1/responses"})
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
        self.app.state.connections.transport = httpx.MockTransport(upstream)
        discovered = await self.client.post(self.path+"/discover", headers=self.csrf, json=self.body)
        self.assertEqual(discovered.json(), {"models": ["org/model"]})
        await self.add()
        models = (await self.client.get("/v1/models", headers=self.api_headers)).json()["data"]
        self.assertTrue(any(m["id"] == "myapi/org/model" for m in models))
        for endpoint in ("chat/completions", "responses"):
            response = await self.client.post("/v1/"+endpoint, headers=self.api_headers, json={"model": "myapi/org/model", "messages": []})
            self.assertEqual(response.status_code, 200, response.text)
        await self.client.patch(self.path+"/myapi", headers=self.csrf, json={"enabled": False})
        response = await self.client.post("/v1/chat/completions", headers=self.api_headers, json={"model": "myapi/org/model"})
        self.assertEqual(response.status_code, 503)

    async def test_stream_reasoning_and_upstream_errors(self):
        event = {"choices": [{"delta": {"reasoning_content": "Hello", "tool_calls": []}}]}
        actual_tool = {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "test"}}]}}]}
        data = "data: " + json.dumps(event) + "\r\n\r\ndata: " + json.dumps(actual_tool) + "\n\ndata: [DONE]\n\n"
        self.app.state.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=data))
        await self.add()
        response = await self.client.post("/v1/chat/completions", headers=self.api_headers, json={"model": "myapi/org/model", "stream": True})
        self.assertEqual(response.status_code, 200)
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:") and "[DONE]" not in line]
        self.assertEqual(events[0]["choices"][0]["delta"], {"reasoning_content": "Hello"})
        self.assertEqual(events[1], actual_tool)
        self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
        self.app.state.connections.transport = httpx.MockTransport(lambda r: httpx.Response(401, text="private-upstream-key"))
        response = await self.client.post("/v1/chat/completions", headers=self.api_headers, json={"model": "myapi/org/model"})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("private-upstream-key", response.text)

    async def test_monkey_routing_and_protocol_rejection(self):
        response = await self.client.post("/v1/chat/completions", headers=self.api_headers, json={"model": "monkeycode/model", "messages": []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.native.last, ("/monkey/v1/chat/completions", {"model": "model", "messages": []}))
        response = await self.client.post("/v1/responses", headers=self.api_headers, json={"model": "monkeycode/model"})
        self.assertEqual(response.status_code, 400)

    async def test_custom_first_chunk_and_disconnect_cleanup(self):
        await self.add()
        class Stream(httpx.AsyncByteStream):
            closed = False
            async def __aiter__(self):
                yield b'data: {"choices":[{"delta":{"content":"first"}}]}\n\n'
                await asyncio.Event().wait()
            async def aclose(self):
                self.closed = True
        stream = Stream()
        self.app.state.connections.transport = httpx.MockTransport(lambda r: httpx.Response(200,
            headers={"content-type": "text/event-stream"}, stream=stream))
        disconnected = asyncio.Event()
        received = []
        async def receive():
            await disconnected.wait()
            return {"type": "http.disconnect"}
        async def send(event):
            if event.get("body"):
                received.append(event["body"])
                disconnected.set()
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions", "asgi": {"spec_version": "2.0"}}
        await asyncio.wait_for(self.app.state.connections.relay("myapi", scope["path"], b"{}", scope, receive, send), 2)
        self.assertIn(b"first", b"".join(received))
        self.assertTrue(stream.closed)
