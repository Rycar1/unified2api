"""Hold only the response prefix; never replay output already delivered."""
import asyncio
from fastapi import HTTPException
from .diagnostics import ResponseObservation


async def hold_rate_limit(forward,send,path):
    """Return withheld 429 events, or stream a non-retryable response once."""
    queue=asyncio.Queue(maxsize=16)
    async def capture(event): await queue.put(event)
    async def run():
        try: await forward(capture)
        finally:
            if not asyncio.current_task().cancelling(): await queue.put(None)
    task=asyncio.create_task(run())
    pending=[];size=0;committed=False
    observation=ResponseObservation(path)
    try:
        while True:
            event=await queue.get()
            if event is None:
                await task
                if not committed:
                    final=observation.finish()
                    if not observation.produced and (observation.status==429 or observation.error and final.get('error_category') in {'rate_limited','quota_exhausted'}):
                        return pending
                    for buffered in pending: await send(buffered)
                return None
            observation.feed(event)
            if committed:
                await send(event)
                continue
            pending.append(event);size+=len(event.get('body',b''))
            if not observation.produced and size>(65536 if observation.stream else 8*1024*1024):
                raise HTTPException(502,'上游在有效响应前发送了过多数据')
            # A JSON error can split across body events. Inspect only on completion.
            if observation.completed: observation.finish()
            if observation.produced or observation.terminal and not observation.error and observation.stream:
                committed=True
            elif observation.error or observation.completed:
                limited=observation.status==429 or observation.error_info.get('error_category') in {'rate_limited','quota_exhausted'}
                if not limited: committed=True
            if committed:
                for buffered in pending: await send(buffered)
                pending.clear()
    except HTTPException as exc:
        if committed: exc.account_response_started=True
        raise
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)


async def emit_events(events,send):
    for event in events: await send(event)
