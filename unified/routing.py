"""Provider dispatch and adaptive failover; never replay an emitted answer."""
import asyncio
from contextlib import nullcontext
import json
import os
import time
from urllib.parse import quote
from fastapi import HTTPException
from .adaptive import AdaptiveRoutes, availability
from .diagnostics import CURRENT_TRACE, ResponseObservation, note_account


async def inventory(api):
    cached = getattr(api,'inventory_cache',None)
    if cached and cached[0]>time.monotonic()-5:
        return cached[1]
    result = {}
    async def native_rows(provider,path):
        try:
            code,data = await api.native.request('GET',path)
            if code==200:
                rows = data.get('accounts',[])
                result[provider] = availability(rows)
                if provider=='trae' and os.getenv('TW2A_WORK_MODE','auto')!='disabled':
                    work = availability([{**row,'remaining':row.get('work_credits') or None,
                                          'cooling':row.get('work_cooling',False)} for row in rows])
                    result[provider]['available'] |= work['available']
        except (RuntimeError,ValueError):
            pass
    with api.buddy.state.store.lock:
        result['codebuddy'] = availability(api.buddy.state.pool.rows(api.buddy.state.store.account_rows()))
    await asyncio.gather(native_rows('trae','/admin/api/accounts'),native_rows('monkeycode','/monkey/admin/accounts'))
    if api.qoder:
        rows = await api.qoder.accounts()
        for region,provider in [('cn','qoder-cn'),('global','qoder')]:
            result[provider] = availability([row for row in rows if row['region']==region])
    if api.workbuddy:
        try:
            result['workbuddy'] = availability(await asyncio.wait_for(api.workbuddy.public_accounts(),4))
        except (HTTPException,asyncio.TimeoutError):
            pass
    if api.connections:
        for row in api.connections.rows():
            result[row['id']] = api.connections.pool_inventory(row['id'])
    api.inventory_cache = (time.monotonic(),result)
    return result


async def dispatch(api,provider,scope,receive,send,body):
    state = scope.setdefault('state',{})
    state.pop('selected_account',None)
    trace = CURRENT_TRACE.get()
    parsed = json.loads(body)
    full_model = provider+'/'+parsed.get('model','')
    span = trace.begin(full_model) if trace else None
    observation = ResponseObservation(scope['path'])
    if trace:
        trace.current_response = observation
    async def observe(message):
        observation.feed(message)
        account = state.get('selected_account')
        if account and span is not None:
            span.update(account_id=account['id'],account_name=account.get('name',''))
        await send(message)
    async def forward(emit):
        if provider=='trae':
            return await api.stream_native(scope,receive,emit,body)
        if provider=='monkeycode':
            return await api.stream_native({**scope,'path':'/monkey'+scope['path']},receive,emit,body)
        if provider in {'qoder','qoder-cn'}:
            return await api.qoder.relay(scope['path'],body,scope,receive,emit)
        if provider=='workbuddy':
            return await api.workbuddy.relay(scope['path'],body,scope,receive,emit)
        if provider!='codebuddy':
            return await api.connections.relay(provider,scope['path'],body,scope,receive,emit)
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        child = {**scope,'headers':[(k,v) for k,v in scope['headers'] if k.lower() not in {b'content-length',b'cookie'}]}
        child['headers'].append((b'content-length',str(len(body)).encode()))
        return await api.buddy(child,replay,emit)
    try:
        network = getattr(api, 'network', None)
        if network and provider in {'trae', 'monkeycode', 'codebuddy', 'workbuddy', 'qoder', 'qoder-cn'}:
            from .network import guarded_response
            return await guarded_response(forward, observe, scope['path'], network.get())
        return await forward(observe)
    except HTTPException as exc:
        observation.status,observation.error,observation.completed = exc.status_code,True,True
        from .error_diagnosis import classify_error
        observation.error_info=classify_error(exc.detail,exc.status_code)
        raise
    finally:
        final = observation.finish()
        state['last_observation'] = final
        if span is not None:
            span.update({key:value for key,value in final.items() if key!='ok'})
            span.update(observation.usage)
            trace.current_response = None


async def route_dispatch(api,rid,scope,receive,send,data):
    route = api.routes.get(rid)
    snapshot = await inventory(api) if route['strategy']=='smart' else {}
    by_model = {target:snapshot.get(target.partition('/')[0],{}) for target in route['targets']}
    route,targets = api.routes.candidates(rid,by_model) if isinstance(api.routes,AdaptiveRoutes) else api.routes.candidates(rid)
    capabilities = getattr(api,'capabilities',None)
    if capabilities and scope['path']!='/v1/responses/compact':
        protocol = {'/v1/chat/completions':'chat','/v1/responses':'responses','/v1/messages':'messages'}[scope['path']]
        targets = [target for target in targets if capabilities.describe({'id':target})['protocols'][protocol]!='unsupported']
    if scope['path']=='/v1/responses/compact':
        targets = [target for target in targets if api.capabilities.describe({'id':target}).get('compact') is True]
    if not targets:
        raise HTTPException(503,'路由没有余额、健康状态和并发均可用的目标')
    trace = CURRENT_TRACE.get()
    if trace:
        trace.smart = route['strategy']=='smart'
        trace.max_account_concurrency = route.get('max_account_concurrency',2)
    scope.setdefault('state',{})['smart'] = route['strategy']=='smart'
    scope['state']['max_account_concurrency'] = route.get('max_account_concurrency',2)
    for index,target in enumerate(targets):
        scope['state'].pop('last_observation',None)
        body = json.dumps({**data,'model':target},ensure_ascii=False,allow_nan=False).encode()
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        child = {**scope,'headers':[(k,v) for k,v in scope['headers'] if k.lower()!=b'content-length']}
        child['headers'].append((b'content-length',str(len(body)).encode()))
        started = time.monotonic()
        status,ok = 502,False
        try:
            with api.routes.reserve(target,route) if isinstance(api.routes,AdaptiveRoutes) else nullcontext():
                if data.get('stream'):
                    queue = asyncio.Queue(maxsize=16)
                    async def capture(message):
                        await queue.put(message)
                    async def run():
                        try:
                            await api(child,replay,capture)
                        finally:
                            if asyncio.current_task().cancelling():
                                try:
                                    queue.put_nowait(None)
                                except asyncio.QueueFull:
                                    pass
                            else:
                                await queue.put(None)
                    task = asyncio.create_task(run())
                    pending = []
                    prefix = ResponseObservation(scope['path'])
                    ended = False
                    try:
                        while True:
                            event = await queue.get()
                            if event is None:
                                ended = True
                                break
                            pending.append(event)
                            prefix.feed(event)
                            if event['type']=='http.response.start':
                                status = event['status']
                                if not 200<=status<300:
                                    break
                            elif event['type']=='http.response.body':
                                if prefix.error or prefix.produced or prefix.terminal or prefix.completed:
                                    break
                                if sum(len(item.get('body',b'')) for item in pending)>65536:
                                    raise HTTPException(502,'上游未开始有效响应')
                        from .server import response_failed
                        response_body = b''.join(event.get('body',b'') for event in pending)
                        ok = 200<=status<300 and not response_failed(response_body[:65536])
                        if ok or index==len(targets)-1:
                            scope['state']['resolved_model'] = target
                            for event in pending:
                                await send(event)
                            while not ended:
                                event = await queue.get()
                                if event is None:
                                    break
                                await send(event)
                            await task
                            final = scope['state'].get('last_observation',{})
                            ok = final.get('ok',ok)
                            return
                    finally:
                        task.cancel()
                        await asyncio.gather(task,return_exceptions=True)
                else:
                    events = []
                    async def capture(message):
                        events.append(message)
                    await api(child,replay,capture)
                    status = next((event['status'] for event in events if event['type']=='http.response.start'),502)
                    from .server import response_failed
                    value = b''.join(event.get('body',b'') for event in events)
                    ok = 200<=status<300 and not response_failed(value[:65536])
                    if ok or index==len(targets)-1:
                        scope['state']['resolved_model'] = target
                        for event in events:
                            await send(event)
                        return
        except HTTPException as exc:
            status = exc.status_code
            if index==len(targets)-1:
                raise
        finally:
            observation = scope['state'].get('last_observation',{})
            args = (target,ok,status,(time.monotonic()-started)*1000,route['cooldown_seconds'])
            api.routes.record(*args,observation.get('ttft_ms')) if isinstance(api.routes,AdaptiveRoutes) else api.routes.record(*args)
    raise HTTPException(503,'所有路由目标均不可用')
