import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx
from admin.server import create_app as create_buddy
from unified.server import create_app
from unified.custom import Connections


class Native:
    def __init__(self):
        self.requests = []
        self.monkey_balance = False
        self.trae_remaining = None
        self.next_trae_balance = 2000
        self.monkey_remaining = 12500
        self.balance_error = False

    async def request(self, method, path, body=b""):
        self.requests.append((method, path))
        if path == "/admin/api/accounts":
            return 200, {"accounts": [{"uid": "test", "nickname": "Test", "enabled": True, "disabled": False, "cooling": False, "expires_at": 0, "remaining": self.trae_remaining}]}
        if path == "/admin/api/accounts/test/balance":
            if self.balance_error:
                return 502, {"error": {"message": "balance query failed"}}
            self.trae_remaining = self.next_trae_balance
            return 200, {"uid": "test", "ok": True, "message": "updated", "remaining": self.trae_remaining}
        if path == "/monkey/admin/accounts/test/refresh":
            if self.balance_error:
                return 502, {"detail": "balance query failed"}
            self.monkey_balance = True
        if path == "/monkey/admin/accounts" and self.monkey_balance:
            return 200, {"accounts": [{"uid": "test", "balance": self.monkey_remaining, "daily_token_balance": 900, "disabled": False, "cooling": False}]}
        if path == "/admin/api/checkin":
            return 200, {"results": [{"uid": "trae-test", "ok": True, "message": "今日已签到"}]}
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
        self.buddy = buddy
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

    async def test_unified_checkin_requires_csrf_and_aggregates_platforms(self):
        path = "/admin/api/unified/checkin"
        self.assertEqual((await self.client.post(path)).status_code, 403)
        response = await self.client.post(path, headers=self.csrf, json={})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual([item["provider"] for item in result["providers"]], ["trae", "codebuddy", "monkeycode"])
        self.assertEqual(result["summary"], {"total": 1, "succeeded": 1, "failed": 0})

    async def test_per_account_trae_and_monkey_actions_require_csrf(self):
        cases = [
            ("/admin/api/unified/trae/accounts/test/checkin", "/admin/api/accounts/test/checkin"),
            ("/admin/api/unified/trae/accounts/test/refresh", "/admin/api/accounts/test/refresh"),
            ("/admin/api/unified/trae/accounts/test/balance", "/admin/api/accounts/test/balance"),
            ("/admin/api/unified/monkeycode/accounts/test/checkin", "/monkey/admin/accounts/test/checkin"),
            ("/admin/api/unified/monkeycode/accounts/test/refresh", "/monkey/admin/accounts/test/refresh"),
            ("/admin/api/unified/monkeycode/accounts/test/balance", "/monkey/admin/accounts/test/refresh"),
        ]
        for path, upstream in cases:
            self.assertEqual((await self.client.post(path)).status_code, 403)
            response = await self.client.post(path, headers=self.csrf, json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn(("POST", upstream), self.native.requests)

        balance = await self.client.post("/admin/api/unified/accounts/trae/test/balance", headers=self.csrf, json={})
        self.assertEqual(balance.json()["remaining"], 2000)
        overview = await self.client.get("/admin/api/unified/overview")
        trae = next(item for item in overview.json()["accounts"] if item["provider"] == "trae")
        self.assertEqual(trae["remaining"], 2000)

    async def test_balance_reads_latest_provider_snapshot_including_zero(self):
        overview_path = "/admin/api/unified/overview"
        async def row(provider):
            overview = (await self.client.get(overview_path)).json()
            return next(item for item in overview["accounts"] if item["provider"] == provider)
        self.assertIsNone((await row("trae"))["remaining"])
        for provider in ("trae", "monkeycode"):
            path = f"/admin/api/unified/accounts/{provider}/test/balance"
            before = len(self.native.requests)
            self.assertEqual((await self.client.post(path)).status_code, 403)
            self.assertEqual(len(self.native.requests), before)
            response = await self.client.post(path, headers=self.csrf, json={})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await row(provider))["remaining"], response.json()["remaining"])
        # A later provider refresh must supersede a previously queried value.
        self.native.trae_remaining = 125
        self.native.monkey_remaining = 12000
        self.assertEqual((await row("trae"))["remaining"], 125)
        self.assertEqual((await row("monkeycode"))["remaining"], 12)
        self.native.next_trae_balance = self.native.monkey_remaining = 0
        for provider in ("trae", "monkeycode"):
            response = await self.client.post(f"/admin/api/unified/accounts/{provider}/test/balance", headers=self.csrf, json={})
            self.assertEqual(response.json()["remaining"], 0)
            self.assertEqual((await row(provider))["remaining"], 0)
        self.native.balance_error = True
        for provider in ("trae", "monkeycode"):
            response = await self.client.post(f"/admin/api/unified/accounts/{provider}/test/balance", headers=self.csrf, json={})
            self.assertEqual(response.status_code, 502)
            self.assertEqual((await row(provider))["remaining"], 0)

    async def test_codebuddy_balance_refresh_and_failed_query_preserve_snapshot(self):
        credential = {"account": {"uid": "buddy-test", "nickname": "Test", "enterpriseId": "test"},
                      "auth": {"accessToken": "fake-access", "refreshToken": "fake-refresh", "expiresAt": 2000000000000}}
        created = await self.client.post("/admin/api/accounts", headers=self.csrf, json={"credential": credential})
        aid = created.json()["id"]
        store, pool = self.buddy.state.store, self.buddy.state.pool
        store.manager_for(aid, store.data["accounts"][aid]).get_headers = Mock(return_value={"X-Domain": "www.codebuddy.cn"})
        state = {"balance": "12.34", "fail": False}
        def upstream(req):
            if state["fail"]:
                return httpx.Response(500)
            if req.url.path.endswith("checkin-activity-status"):
                return httpx.Response(200, json={"code": 0, "data": {"active": True, "today_checked_in": True}})
            return httpx.Response(200, json={"code": 0, "data": {"Response": {"Data": {"TotalCount": 1, "Accounts": [
                {"CycleCapacityRemainPrecise": state["balance"], "CycleCapacitySize": 500}
            ]}}}})
        pool.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(upstream))
        path = f"/admin/api/unified/accounts/codebuddy/{aid}/balance"
        self.assertEqual((await self.client.post(path)).status_code, 403)
        for value in ("12.34", "0"):
            state["balance"] = value
            response = await self.client.post(path, headers=self.csrf, json={})
            self.assertTrue(response.json()["ok"], response.text)
            overview = (await self.client.get("/admin/api/unified/overview")).json()
            account = next(item for item in overview["accounts"] if item["provider"] == "codebuddy")
            self.assertEqual(account["remaining"], float(value))
        state["fail"] = True
        self.assertFalse((await self.client.post(path, headers=self.csrf, json={})).json()["ok"])
        self.assertEqual(pool.rows(store.account_rows())[0]["remaining"], 0)

    async def test_test_endpoint_returns_upstream_diagnostics_and_cleans_test_key(self):
        await self.add()
        cases = [
            (httpx.Response(403, json={"detail": {"error": {"message": "model access denied", "code": "permission_denied"}}}), "model access denied"),
            (httpx.Response(502, text="gateway timeout: backend unavailable"), "backend unavailable"),
            (httpx.Response(401, text="invalid key: private-upstream-key"), "invalid key"),
            (httpx.Response(200, json={"error": {"message": "invalid credentials private-upstream-key"}}), "invalid credentials"),
        ]
        for upstream, expected in cases:
            self.app.state.connections.transport = httpx.MockTransport(lambda req: upstream)
            response = await self.client.post("/admin/api/unified/test", headers=self.csrf, json={"model": "myapi/org/model", "message": "hello"})
            self.assertEqual(response.status_code, 200, response.text)
            data = response.json()
            self.assertFalse(data["ok"])
            self.assertIn(expected, data["error"])
            self.assertIn(str(upstream.status_code), data["error"])
            self.assertNotIn("private-upstream-key", data["error"])
            self.assertFalse(self.buddy.state.store.test_keys)

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
