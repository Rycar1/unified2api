import json
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi import HTTPException

from unified.custom import Connections
from unified.errors import safe_error_message


class SafeErrorsTest(unittest.TestCase):
    def test_nested_json_and_plain_text_keep_diagnostics(self):
        nested = {"detail": {"error": {"message": "Model alpha is not available", "code": "model_not_found"}}, "request": {"token": "private"}}
        actual = safe_error_message(nested, 403)
        self.assertEqual(actual, "HTTP 403：Model alpha is not available；code=model_not_found")
        self.assertEqual(safe_error_message(json.dumps(nested), 403), actual)
        self.assertEqual(safe_error_message("upstream websocket handshake failed: 403", 502), "HTTP 502：upstream websocket handshake failed: 403")
        self.assertEqual(safe_error_message({"detail": "HTTP 403：not allowed"}, 403), "HTTP 403：not allowed")

    def test_quoted_fields_and_multiple_cookies_are_redacted(self):
        value = '''quota exceeded; "api_key": "private-key"; 'access_token': 'private-access'; Authorization: Bearer private-bearer; Cookie: session=private-cookie; tracking=private-tracker; model unavailable'''
        actual = safe_error_message(value, 429)
        for secret in ("private-key", "private-access", "private-bearer", "private-cookie", "private-tracker"):
            self.assertNotIn(secret, actual)
        self.assertIn("quota exceeded", actual)
        self.assertIn("model unavailable", actual)

    def test_quoted_cookie_header_and_session_are_redacted(self):
        value = '''authentication failed; "Cookie": "one=secret-one; two=secret-two"; monkeycode_ai_session=private-session; accessToken="private-camel"; retry after login'''
        actual = safe_error_message(value, 401)
        for secret in ("secret-one", "secret-two", "private-session", "private-camel"):
            self.assertNotIn(secret, actual)
        self.assertIn("authentication failed", actual)
        self.assertIn("retry after login", actual)

    def test_known_secrets_bare_escaped_and_truncated(self):
        secret = 'upstream/key"value'
        for value in (secret, json.dumps(secret)[1:-1], "upstream%2Fkey%22value", secret[:-2]):
            actual = safe_error_message("invalid credential: " + value, 401, (secret,))
            self.assertNotIn(value, actual)
            self.assertIn("invalid credential", actual)

    def test_truncated_quoted_fields_are_redacted(self):
        actual = safe_error_message('model unavailable; "key": "private-key-without-closing-quote', 403)
        self.assertIn("model unavailable", actual)
        self.assertNotIn("private-key", actual)

    def test_limits_and_unknown_json(self):
        self.assertLessEqual(len(safe_error_message("long error " * 1000, 500)), 1210)
        self.assertEqual(safe_error_message({"headers": {"Authorization": "private"}}, 502), "HTTP 502：上游没有返回错误详情")
        self.assertEqual(safe_error_message({"detail": [{"msg": "required field"}]}, 422), "HTTP 422：required field")


class CustomErrorsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.connections = Connections(Path(self.temp.name) / "connections.json")
        self.row = {"id": "test", "name": "test", "base_url": "https://example.test", "key": "private-upstream-key", "models": ["alpha"]}
        self.connections.save(self.row)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_discovery_keeps_specific_error_and_hides_key(self):
        self.connections.transport = httpx.MockTransport(lambda request: httpx.Response(403, json={"detail": {"error": {"message": "No model access for private-upstream-key", "code": "forbidden_model"}}}))
        with self.assertRaises(HTTPException) as caught:
            await self.connections.discover(self.row)
        self.assertEqual(caught.exception.status_code, 502)
        self.assertIn("HTTP 403", caught.exception.detail)
        self.assertIn("No model access", caught.exception.detail)
        self.assertIn("forbidden_model", caught.exception.detail)
        self.assertNotIn(self.row["key"], caught.exception.detail)

    async def test_discovery_error_with_success_status_is_visible(self):
        self.connections.transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"error": "Access denied for private-upstream-key"}))
        with self.assertRaises(HTTPException) as caught:
            await self.connections.discover(self.row)
        self.assertIn("Access denied", caught.exception.detail)
        self.assertNotIn(self.row["key"], caught.exception.detail)

    async def test_relay_non_json_error_is_visible(self):
        self.connections.transport = httpx.MockTransport(lambda request: httpx.Response(429, text="rate limit exceeded for private-upstream-key; retry in 60 seconds"))
        with self.assertRaises(HTTPException) as caught:
            await self.connections.relay("test", "/v1/chat/completions", b"{}", {}, None, None)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("rate limit exceeded", caught.exception.detail)
        self.assertIn("retry in 60 seconds", caught.exception.detail)
        self.assertNotIn(self.row["key"], caught.exception.detail)

    async def test_large_error_stops_reading_and_closes_stream(self):
        class Stream(httpx.AsyncByteStream):
            count = 0
            closed = False

            async def __aiter__(self):
                self.count += 1
                yield b"rate limit exceeded\n" + b"x" * 65536
                self.count += 1
                yield b"must not read this part"

            async def aclose(self):
                self.closed = True

        stream = Stream()
        self.connections.transport = httpx.MockTransport(lambda request: httpx.Response(429, stream=stream))
        with self.assertRaises(HTTPException) as caught:
            await self.connections.relay("test", "/v1/chat/completions", b"{}", {}, None, None)
        self.assertIn("rate limit exceeded", caught.exception.detail)
        self.assertEqual(stream.count, 1)
        self.assertTrue(stream.closed)
