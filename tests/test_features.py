import asyncio
import base64
import json
import tempfile
import threading
import unittest
from pathlib import Path

from fastapi import HTTPException

from unified.features import AutomationManager, BackupManager, RequestLog, RequestLogMiddleware, RouteManager, UnifiedConfig
from unified.server import API


class FakeStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.data = {}
        self.saved = 0

    def save(self):
        self.saved += 1


class FeatureStores(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.config = UnifiedConfig(self.store)

    def test_routes_validate_select_and_cooldown(self):
        clock = [1000]
        routes = RouteManager(self.config, clock=lambda: clock[0])
        route = routes.save({"id": "smart", "name": "Smart", "targets": ["a/one", "b/two"],
                             "strategy": "priority", "retries": 1, "cooldown_seconds": 60}, {"a/one", "b/two"})
        self.assertEqual(route["id"], "smart")
        self.assertEqual(routes.candidates("smart")[1], ["a/one", "b/two"])
        routes.record("a/one", False, 429, 10, 60)
        self.assertEqual(routes.candidates("smart")[1], ["b/two"])
        clock[0] += 61
        self.assertEqual(routes.candidates("smart")[1], ["a/one", "b/two"])
        with self.assertRaises(HTTPException):
            routes.save({"id": "loop", "targets": ["route/smart"]}, {"route/smart"})

    def test_edit_route_keeps_id_and_target_order(self):
        routes = RouteManager(self.config)
        available = {"a/one", "b/two"}
        routes.save({"id": "smart", "name": "Old", "targets": ["a/one", "b/two"]}, available)
        updated = routes.save({"id": "smart", "name": "New", "targets": ["b/two", "a/one"],
                               "strategy": "round_robin", "retries": 1, "cooldown_seconds": 90,
                               "enabled": False}, available, "smart")
        self.assertEqual(updated["targets"], ["b/two", "a/one"])
        self.assertEqual(updated["name"], "New")
        self.assertFalse(updated["enabled"])
        self.assertEqual(len(routes.rows()), 1)
        self.assertEqual(routes.public_models(), [])
        for body, current_id, status in [
            ({"id": "other", "targets": ["a/one"]}, "smart", 400),
            ({"id": "missing", "targets": ["a/one"]}, "missing", 404),
        ]:
            with self.assertRaises(HTTPException) as error:
                routes.save(body, available, current_id)
            self.assertEqual(error.exception.status_code, status)
        self.assertEqual(routes.rows()[0]["id"], "smart")

    def test_route_prefix_accepts_model_version_dot(self):
        routes = RouteManager(self.config)
        row = routes.save({"id": "glm-5.3", "targets": ["a/one"]}, {"a/one"})
        self.assertEqual(row["id"], "glm-5.3")
        self.assertEqual(routes.public_models()[0]["id"], "route/glm-5.3")
        for bad in ("Glm-5.3", "glm/5.3", ".glm-5.3"):
            with self.assertRaises(HTTPException):
                routes.save({"id": bad, "targets": ["a/one"]}, {"a/one"})

    def test_request_log_persists_metadata_and_usage(self):
        with tempfile.TemporaryDirectory() as root:
            log = RequestLog(Path(root) / "requests.sqlite")
            log.record({"path": "/v1/chat/completions", "model": "route/smart",
                        "resolved_model": "a/one", "status": 200, "ok": True,
                        "duration_ms": 125.4, "outcome": "success", "prompt_tokens": 10,
                        "completion_tokens": 20, "reasoning_tokens": 8})
            result = log.query()
            self.assertEqual(result["total"], 1)
            self.assertEqual(result["items"][0]["reasoning_tokens"], 8)
            self.assertEqual(log.summary()["success_rate"], 100.0)

    def test_encrypted_backup_round_trip_and_wrong_password(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            (source / "credential.json").write_text('{"secret":"one"}', encoding="utf-8")
            backup = BackupManager({"management": source})
            blob = backup.export("correct horse")
            self.assertNotIn(b"secret", blob)
            (source / "credential.json").write_text("changed", encoding="utf-8")
            backup.restore(base64.b64encode(blob).decode(), "correct horse")
            self.assertIn("one", (source / "credential.json").read_text(encoding="utf-8"))
            with self.assertRaises(HTTPException):
                backup.restore(base64.b64encode(blob).decode(), "wrong password")

    def test_automation_validation(self):
        manager = AutomationManager(self.config, lambda action: None)
        updated = manager.update({"auto_checkin": True, "checkin_time": "08:30",
                                  "auto_refresh": True, "refresh_minutes": 15,
                                  "webhook_url": "https://example.com/hook", "low_balance": 3})
        self.assertTrue(updated["auto_checkin"])
        with self.assertRaises(HTTPException):
            manager.update({"refresh_minutes": 1})


class RouteDispatch(unittest.IsolatedAsyncioTestCase):
    async def test_named_sse_error_falls_back_before_done(self):
        routes = RouteManager(UnifiedConfig(FakeStore()))
        routes.save({"id": "stable", "targets": ["trae/bad", "codebuddy/good"],
                     "retries": 1}, {"trae/bad", "codebuddy/good"})

        class Upstream(API):
            async def __call__(self, scope, receive, send):
                payload = json.loads((await receive())["body"])
                body = (b'event: error\ndata: "upstream rejected request"\n\ndata: [DONE]\n\n'
                        if payload["model"] == "trae/bad" else
                        b'data: {"choices":[{"delta":{"content":"OK"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/event-stream")]})
                await send({"type": "http.response.body", "body": body, "more_body": False})

        async def receive():
            await asyncio.sleep(3600)

        sent = []
        async def send(message):
            sent.append(message)
        scope = {"path": "/v1/chat/completions", "headers": []}
        await Upstream(None, None, None, routes=routes).route_request(
            "stable", scope, receive, send, {"model": "route/stable", "stream": True})
        self.assertEqual(scope["state"]["resolved_model"], "codebuddy/good")
        self.assertNotIn(b"upstream rejected", b"".join(m.get("body", b"") for m in sent))
        self.assertGreater(routes.health["trae/bad"]["cooldown_until"], 0)

    async def test_failed_target_falls_back_to_next_model(self):
        store = FakeStore()
        store.check_api = lambda *args: None
        config = UnifiedConfig(store)
        routes = RouteManager(config)
        routes.save({"id": "stable", "name": "Stable", "targets": ["codebuddy/bad", "codebuddy/good"],
                     "strategy": "priority", "retries": 1, "cooldown_seconds": 60},
                    {"codebuddy/bad", "codebuddy/good"})

        class Buddy:
            pass
        buddy = Buddy()
        buddy.state = type("State", (), {"store": store})()

        async def call(self, scope, receive, send):
            payload = json.loads((await receive())["body"])
            status = 503 if payload["model"] == "bad" else 200
            body = b'{"error":"unavailable"}' if status != 200 else b'{"choices":[{"message":{"content":"ok"}}]}'
            await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": body, "more_body": False})
        buddy.__class__.__call__ = call

        api = API(None, buddy, None, routes=routes)
        raw = json.dumps({"model": "route/stable", "messages": [{"role": "user", "content": "hi"}]}).encode()
        received = False
        async def receive():
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.sleep(3600)
        sent = []
        async def send(message):
            sent.append(message)
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
                 "query_string": b"", "headers": [(b"authorization", b"Bearer test")]}
        await api(scope, receive, send)
        self.assertEqual(next(item["status"] for item in sent if item["type"] == "http.response.start"), 200)
        self.assertEqual(scope["state"]["resolved_model"], "codebuddy/good")

    async def test_stream_is_forwarded_before_upstream_finishes(self):
        store = FakeStore()
        store.check_api = lambda *args: None
        config = UnifiedConfig(store)
        routes = RouteManager(config)
        routes.save({"id": "live", "name": "Live", "targets": ["codebuddy/good"],
                     "strategy": "priority", "retries": 0, "cooldown_seconds": 60}, {"codebuddy/good"})
        release = asyncio.Event()

        class Buddy:
            async def __call__(self, scope, receive, send):
                await receive()
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"text/event-stream")]})
                await send({"type": "http.response.body", "body": b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n', "more_body": True})
                await release.wait()
                await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})
        buddy = Buddy()
        buddy.state = type("State", (), {"store": store})()
        api = API(None, buddy, None, routes=routes)
        raw = json.dumps({"model": "route/live", "messages": [], "stream": True}).encode()
        used = False
        async def receive():
            nonlocal used
            if not used:
                used = True
                return {"type": "http.request", "body": raw, "more_body": False}
            await asyncio.sleep(3600)
        first = asyncio.Event()
        sent = []
        async def send(message):
            sent.append(message)
            if message.get("body", b"").startswith(b"data:"):
                first.set()
        scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions",
                 "query_string": b"", "headers": [(b"authorization", b"Bearer test")]}
        task = asyncio.create_task(api(scope, receive, send))
        await asyncio.wait_for(first.wait(), .5)
        self.assertFalse(task.done())
        release.set()
        await asyncio.wait_for(task, .5)
        self.assertTrue(any(item.get("body", b"").startswith(b"data: [DONE]") for item in sent))


class StreamIntegrity(unittest.IsolatedAsyncioTestCase):
    async def test_chat_stream_without_finish_reason_is_recorded_as_failed(self):
        with tempfile.TemporaryDirectory() as root:
            log = RequestLog(Path(root) / "requests.sqlite")

            async def run(chunks):
                async def app(scope, receive, send):
                    await receive()
                    scope.setdefault("state", {})["resolved_model"] = "workbuddy/deepseek-v4.1-flash"
                    await send({"type": "http.response.start", "status": 200,
                                "headers": [(b"content-type", b"text/event-stream")]})
                    for index, chunk in enumerate(chunks):
                        await send({"type": "http.response.body", "body": chunk,
                                    "more_body": index < len(chunks) - 1})

                raw = b'{"model":"route/deepseek-v41","stream":true}'
                received = False

                async def receive():
                    nonlocal received
                    if not received:
                        received = True
                        return {"type": "http.request", "body": raw, "more_body": False}
                    await asyncio.sleep(3600)

                async def send(message):
                    pass

                scope = {"type": "http", "method": "POST", "path": "/v1/chat/completions"}
                await RequestLogMiddleware(app, log)(scope, receive, send)
                return log.query(limit=1)["items"][0]

            incomplete = await run([b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\n', b'data: [DONE]\n\n'])
            self.assertFalse(incomplete["ok"])
            self.assertEqual(incomplete["outcome"], "missing_finish_reason")
            self.assertEqual(incomplete["resolved_model"], "workbuddy/deepseek-v4.1-flash")
            complete = await run([b'data: {"choices":[{"finish_', b'reason":"stop"}]}\n\ndata: [DONE]\n\n'])
            self.assertTrue(complete["ok"])
            self.assertEqual(complete["outcome"], "success")
            legacy_error = await run([b'event: error\ndata: "upstream error"\n\ndata: [DONE]\n\n',
                                      b'data: {"choices":[{"finish_reason":"stop"}]}\n\n'])
            self.assertFalse(legacy_error["ok"])
            self.assertEqual(legacy_error["outcome"], "stream_error")
            late_finish = await run([b'data: [DONE]\n\ndata: {"choices":[{"finish_reason":"stop"}]}\n\n'])
            self.assertFalse(late_finish["ok"])
            self.assertEqual(late_finish["outcome"], "missing_finish_reason")


if __name__ == "__main__":
    unittest.main()
