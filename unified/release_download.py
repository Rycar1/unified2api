"""Bounded source downloads with retries and progress; no upstream bodies in errors."""
import asyncio

import httpx


class SourceDownloadError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


async def download_source(repository, revision, on_progress, *, client_factory=None,
                          timeout_seconds=180, attempts=3, retry_delay=1, max_bytes=150*1024*1024):
    factory = client_factory or (lambda: httpx.AsyncClient(
        timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False))
    url = 'https://codeload.github.com/' + repository + '/tar.gz/' + revision
    error_code = 'source_download_failed'
    try:
        async with asyncio.timeout(timeout_seconds):
            for attempt in range(1, attempts + 1):
                # Failed partial archives are discarded before opening a fresh connection.
                blob = bytearray()
                on_progress(0, None, attempt)
                try:
                    async with factory() as client:
                        async with client.stream('GET', url) as response:
                            response.raise_for_status()
                            try:
                                total = int(response.headers.get('content-length', '0'))
                            except ValueError:
                                total = 0
                            if total > max_bytes:
                                raise SourceDownloadError('source_download_too_large')
                            total = total if total > 0 else None
                            on_progress(0, total, attempt)
                            async for part in response.aiter_bytes():
                                if len(blob) + len(part) > max_bytes:
                                    raise SourceDownloadError('source_download_too_large')
                                blob.extend(part)
                                on_progress(len(blob), total, attempt)
                    if not blob:
                        raise SourceDownloadError('source_download_failed')
                    return bytes(blob)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500 and exc.response.status_code != 429:
                        raise SourceDownloadError('source_download_failed') from None
                    error_code = 'source_download_failed'
                except httpx.TimeoutException:
                    error_code = 'source_download_timeout'
                except httpx.HTTPError:
                    error_code = 'source_download_failed'
                if attempt < attempts:
                    await asyncio.sleep(retry_delay)
    except TimeoutError:
        raise SourceDownloadError('source_download_timeout') from None
    raise SourceDownloadError(error_code) from None
