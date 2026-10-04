import unittest
from unittest.mock import Mock, patch

import httpx
from core import converter


class ReasoningPassthroughTest(unittest.IsolatedAsyncioTestCase):
    async def test_deepseek_reasoning_is_only_forwarded_when_client_sets_it(self):
        forwarded = []

        async def stream_upstream(url, headers, body, model, started, request_id):
            forwarded.append(body)
            yield b"data: [DONE]\n\n"

        with patch.object(converter, "_check_auth"), patch.object(
            converter, "_cred", return_value=Mock(get_headers=lambda: {})
        ), patch.object(converter, "_stream_upstream", stream_upstream):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=converter.app), base_url="http://test"
            ) as client:
                base = {"model": "deepseek-v4.1-flash", "messages": [{"role": "user", "content": "hi"}], "stream": True}
                for body in (base, {**base, "reasoning_effort": "low"}):
                    response = await client.post("/v1/chat/completions", json=body)
                    self.assertEqual(response.status_code, 200, response.text)

        self.assertNotIn("reasoning_effort", forwarded[0])
        self.assertEqual(forwarded[1]["reasoning_effort"], "low")
