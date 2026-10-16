import asyncio
import json
import threading
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from unified.adaptive import AdaptiveRoutes
from unified.features import UnifiedConfig
from unified.network import NetworkSettings, guarded_response


def config():
    return UnifiedConfig(SimpleNamespace(lock=threading.RLock(), data={}, save=lambda: None))


class RoutePlans(unittest.TestCase):
    def test_preview_does_not_rotate_and_matches_next_dispatch(self):
        routes = AdaptiveRoutes(config(), clock=lambda: 1000)
        routes.save({'id': 'r', 'strategy': 'round_robin', 'targets': ['a/m', 'b/m']}, {'a/m', 'b/m'})
        routes.candidates('r')
        first = routes.explain('r', {})
        second = routes.explain('r', {})
        self.assertEqual(first['selected'], 'b/m')
        self.assertEqual(first, second)
        self.assertEqual(routes.candidates('r')[1][0], first['selected'])

    def test_smart_plan_explains_cooldown_unavailable_and_concurrency(self):
        routes = AdaptiveRoutes(config(), clock=lambda: 1000)
        targets = ['a/m', 'b/m', 'c/m', 'd/m']
        routes.save({'id': 'r', 'strategy': 'smart', 'targets': targets}, set(targets))
        routes.record('a/m', False, 429, 1, 60)
        routes.active['c/m'] = 8
        plan = routes.explain('r', {'b/m': {'available': False}, 'd/m': {'available': True}})
        self.assertEqual(plan['selected'], 'd/m')
        self.assertEqual({r['model']: r['state'] for r in plan['items']},
                         {'a/m': 'cooldown', 'b/m': 'unavailable', 'c/m': 'concurrency', 'd/m': 'selected'})


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, producer):
        events = []
        async def send(event):
            events.append(event)
        await guarded_response(producer, send, '/v1/chat/completions',
                               {'first_token_seconds': .15, 'idle_seconds': .15})
        return events

    async def test_heartbeats_do_not_hide_first_token_timeout(self):
        cancelled = asyncio.Event()
        async def upstream(send):
            try:
                await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/event-stream')]})
                while True:
                    await send({'type': 'http.response.body', 'body': b': ping\n\n', 'more_body': True})
                    await asyncio.sleep(.005)
            finally:
                cancelled.set()
        with self.assertRaises(HTTPException) as caught:
            await self.invoke(upstream)
        self.assertEqual(caught.exception.status_code, 504)
        self.assertTrue(cancelled.is_set())

    async def test_idle_timeout_after_output_is_sse_error_not_second_http_response(self):
        async def upstream(send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/event-stream')]})
            await send({'type': 'http.response.body', 'body': b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n', 'more_body': True})
            await asyncio.Event().wait()
        events = await self.invoke(upstream)
        self.assertEqual(sum(e['type'] == 'http.response.start' for e in events), 1)
        body = b''.join(e.get('body', b'') for e in events)
        self.assertIn(b'upstream_idle_timeout', body)
        self.assertNotIn(b'"finish_reason":"stop"', body)
        self.assertFalse(events[-1]['more_body'])

    async def test_completed_stream_passes_without_fabrication(self):
        frame = b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
        async def upstream(send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/event-stream')]})
            await send({'type': 'http.response.body', 'body': frame, 'more_body': False})
        events = await self.invoke(upstream)
        self.assertEqual(b''.join(e.get('body', b'') for e in events), frame)

    async def test_network_settings_validate_without_partial_write(self):
        cfg = config()
        settings = NetworkSettings(cfg)
        original = settings.get()
        with self.assertRaises(HTTPException):
            settings.update({'first_token_seconds': 50, 'idle_seconds': -1})
        self.assertEqual(settings.get(), original)

    async def test_large_nonstream_response_is_not_mistaken_for_empty_sse(self):
        body = json.dumps({'choices': [{'message': {'content': 'x' * 200000}}]}).encode()
        async def upstream(send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'application/json')]})
            for offset in range(0, len(body), 16000):
                await send({'type': 'http.response.body', 'body': body[offset:offset + 16000], 'more_body': offset + 16000 < len(body)})
        events = await self.invoke(upstream)
        self.assertEqual(b''.join(e.get('body', b'') for e in events), body)

    async def test_completed_response_does_not_get_a_second_end_frame(self):
        async def upstream(send):
            await send({'type': 'http.response.start', 'status': 200, 'headers': [(b'content-type', b'text/event-stream')]})
            await send({'type': 'http.response.body', 'body': b'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n', 'more_body': False})
            await asyncio.Event().wait()
        events = await self.invoke(upstream)
        self.assertEqual(sum(e['type'] == 'http.response.body' and not e.get('more_body') for e in events), 1)
