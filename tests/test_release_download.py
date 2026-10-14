import asyncio
import time
import unittest

import httpx

from unified.release_download import SourceDownloadError, download_source


class BrokenStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'partial'
        raise httpx.ReadError('private upstream error body')


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        while True:
            yield b'x'
            await asyncio.sleep(.015)


class SourceDownloads(unittest.IsolatedAsyncioTestCase):
    def client(self, reply):
        return lambda: httpx.AsyncClient(transport=httpx.MockTransport(reply))

    async def test_stalled_transfer_retries_with_a_fresh_buffer_and_reports_progress(self):
        calls, progress = [], []
        def reply(request):
            calls.append(request.url)
            if len(calls) == 1:
                return httpx.Response(200, stream=BrokenStream())
            return httpx.Response(200, content=b'complete-source')
        result = await download_source('synthetic/project', '1'*40, lambda *args: progress.append(args),
            client_factory=self.client(reply), retry_delay=0)
        self.assertEqual(result, b'complete-source')
        self.assertEqual(len(calls), 2)
        self.assertIn((0, None, 1), progress)
        self.assertIn((0, None, 2), progress)
        self.assertEqual(progress[-1], (15, 15, 2))

    async def test_total_deadline_stops_transfer_even_when_bytes_keep_arriving(self):
        started = time.monotonic()
        with self.assertRaises(SourceDownloadError) as caught:
            await download_source('synthetic/project', '1'*40, lambda *args: None,
                client_factory=self.client(lambda request: httpx.Response(200, stream=SlowStream())),
                timeout_seconds=.05, retry_delay=0)
        self.assertEqual(caught.exception.code, 'source_download_timeout')
        self.assertLess(time.monotonic()-started, .5)

    async def test_missing_archive_fails_without_retrying_or_exposing_response_text(self):
        calls = []
        def reply(request):
            calls.append(request.url)
            return httpx.Response(404, text='private response body')
        with self.assertRaises(SourceDownloadError) as caught:
            await download_source('synthetic/project', '1'*40, lambda *args: None,
                client_factory=self.client(reply), retry_delay=0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(caught.exception.code, 'source_download_failed')
        self.assertNotIn('private', str(caught.exception))

    async def test_archive_limit_applies_to_streamed_bytes_without_a_length_header(self):
        with self.assertRaises(SourceDownloadError) as caught:
            await download_source('synthetic/project', '1'*40, lambda *args: None,
                client_factory=self.client(lambda request: httpx.Response(200, stream=BrokenStream())),
                max_bytes=3, retry_delay=0)
        self.assertEqual(caught.exception.code, 'source_download_too_large')
