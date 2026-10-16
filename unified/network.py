"""Per-attempt response deadlines, without replaying any emitted content."""
import asyncio
import json
import time

from fastapi import HTTPException
from .diagnostics import ResponseObservation


class NetworkSettings:
    DEFAULT = {'first_token_seconds': 300, 'idle_seconds': 120}

    def __init__(self, config):
        self.config = config

    def get(self):
        return {**self.DEFAULT, **self.config.get('network_settings', {})}

    def update(self, body):
        if not isinstance(body, dict) or set(body) - set(self.DEFAULT):
            raise HTTPException(400, '连接设置包含不支持的字段')
        value = {**self.get(), **body}
        for key, number in value.items():
            if type(number) is not int or not 5 <= number <= 3600:
                raise HTTPException(400, '首字等待和流式空闲超时需要 5–3600 秒')
        self.config.set('network_settings', value)
        return value


async def guarded_response(producer, send, path, settings):
    queue = asyncio.Queue(maxsize=16)

    async def capture(event):
        await queue.put(event)

    async def run():
        try:
            await producer(capture)
        finally:
            if asyncio.current_task().cancelling():
                try:
                    queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
            else:
                await queue.put(None)

    task = asyncio.create_task(run())
    observation = ResponseObservation(path)
    pending, pending_size = [], 0
    started = False
    deadline = time.monotonic() + settings['first_token_seconds']
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), max(.001, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                code = 'upstream_idle_timeout' if started else 'upstream_first_token_timeout'
                detail = '上游流式响应超时，连接已关闭' if started else '等待上游首个有效响应超时'
                if not started:
                    raise HTTPException(504, detail)
                payload = {'error': {'type': code, 'code': code, 'message': detail}}
                await send({'type': 'http.response.body', 'body':
                            ('event: error\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode(),
                            'more_body': False})
                return
            if event is None:
                try:
                    await task
                except Exception as exc:
                    if not started:
                        if isinstance(exc, HTTPException):
                            raise
                        raise HTTPException(502, '上游连接异常，未收到完整响应') from None
                    payload = {'error': {'type': 'upstream_stream_error', 'code': 'upstream_stream_error',
                                         'message': '上游连接中断，请检查调用记录中的实际账号和状态'}}
                    await send({'type': 'http.response.body', 'body':
                                ('event: error\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n').encode(),
                                'more_body': False})
                    return
                for buffered in pending:
                    await send(buffered)
                return
            observation.feed(event)
            if started:
                await send(event)
                if event.get('body'):
                    deadline = time.monotonic() + settings['idle_seconds']
            else:
                pending.append(event)
                pending_size += len(event.get('body', b''))
                if observation.produced or observation.terminal or observation.error or observation.completed:
                    for buffered in pending:
                        await send(buffered)
                    pending.clear()
                    started = True
                    deadline = time.monotonic() + settings['idle_seconds']
                elif pending_size > (65536 if observation.stream else 8 * 1024 * 1024):
                    raise HTTPException(502, '上游在有效响应前发送了过多空数据')
            if observation.completed:
                return
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
