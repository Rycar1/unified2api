"""Provider dispatch and adaptive failover; never replay an emitted answer."""
import asyncio
from collections import Counter
from contextlib import nullcontext
import json
import os
import time
from urllib.parse import quote
from fastapi import HTTPException
from .adaptive import AdaptiveRoutes, availability, model_inventory
from .diagnostics import CURRENT_TRACE, ResponseObservation, note_account


async def inventory(api):
    cached = getattr(api,'inventory_cache',None)
    if cached and cached[0]>time.monotonic()-5:
        return cached[1]
    task = getattr(api,'inventory_task',None)
    if task is None or task.done():
        task = api.inventory_task = asyncio.create_task(refresh_inventory(api))
    # One disconnected caller must not cancel the shared account snapshot.
    return await asyncio.shield(task)


async def refresh_inventory(api):
    result = {}
    async def native_rows(provider,path):
        try:
            code,data = await asyncio.wait_for(api.native.request('GET',path),4)
            if code==200:
                rows = data.get('accounts',[])
                result[provider] = availability(rows)
                if provider=='trae' and os.getenv('TW2A_WORK_MODE','auto')!='disabled':
                    work = availability([{**row,'remaining':row.get('work_credits') or None,
                                          'cooling':row.get('work_cooling',False)} for row in rows])
                    result[provider]['available'] |= work['available']
        except (HTTPException,RuntimeError,ValueError,asyncio.TimeoutError):
            pass
    with api.buddy.state.store.lock:
        result['codebuddy'] = availability(api.buddy.state.pool.rows(api.buddy.state.store.account_rows()))
    async def qoder_rows():
        if api.qoder:
            rows = await asyncio.wait_for(api.qoder.accounts(),4)
            for region,provider in [('cn','qoder-cn'),('global','qoder')]:
                result[provider] = availability([row for row in rows if row['region']==region])
    async def workbuddy_rows():
        if not api.workbuddy:
            return
        try:
            result['workbuddy'] = availability(await asyncio.wait_for(api.workbuddy.public_accounts(),4))
        except (HTTPException,asyncio.TimeoutError):
            pass
    if api.connections:
        for row in api.connections.rows():
            result[row['id']] = api.connections.pool_inventory(row['id'])
    async def antigravity_rows():
        if getattr(api,'antigravity',None):
            # Each model group has its own percentage; the smallest group must
            # not disable every other Gemini model. The pool gates per model.
            rows = await asyncio.wait_for(api.antigravity.accounts(),4)
            result['antigravity'] = availability([{**row,'remaining':None} for row in rows])
    # An unavailable platform is unknown, not evidence that every route is down.
    await asyncio.gather(native_rows('trae','/admin/api/accounts'),
                         native_rows('monkeycode','/monkey/admin/accounts'),
                         qoder_rows(),workbuddy_rows(),antigravity_rows(),return_exceptions=True)
    api.inventory_cache = (time.monotonic(),result)
    return result


async def dispatch(api,provider,scope,receive,send,body):
    if provider=='workbuddy' and not scope.get('state',{}).get('account_test') and hasattr(api.workbuddy,'routing_accounts'):
        return await workbuddy_dispatch(api,scope,receive,send,body)
    pool=getattr(getattr(getattr(api,'buddy',None),'state',None),'pool',None)
    if provider!='codebuddy' or pool is None or scope.get('state',{}).get('account_test'):
        return await dispatch_once(api,provider,scope,receive,send,body)
    from .account_retry import hold_rate_limit,emit_events
    state=scope.setdefault('state',{})
    tried=set();last=None
    model=json.loads(body).get('model','')
    state['account_model']=model
    try:
        while True:
            state['account_exclude']=tried.copy()
            if last:
                with pool.store.lock:
                    rows=[r for r in pool.store.account_rows() if r['id'] not in tried and pool.state(r,model)=='available']
                    if pool.store.data['pool']['routing']=='manual':
                        rows=[r for r in rows if r['id']==pool.store.data['active']]
                if not rows: return await emit_events(last,send)
            async def forward(emit):
                return await dispatch_once(api,provider,scope,receive,emit,body)
            held=await hold_rate_limit(forward,send,scope['path'])
            if held is None: return
            aid=state.get('selected_account',{}).get('id')
            if not aid or aid in tried: return await emit_events(held,send)
            tried.add(aid);last=held
            api.inventory_cache=None
    finally:
        state.pop('account_exclude',None)
        state.pop('account_model',None)


async def workbuddy_dispatch(api,scope,receive,send,body):
    from .account_retry import hold_rate_limit,emit_events
    from .workbuddy_gates import REASONS
    wb=api.workbuddy;tried=set();last=None;last_error=None;rejected=Counter()
    state=scope.setdefault('state',{});payload=json.loads(body);model=payload.get('model','')
    metadata=payload.get('metadata') if isinstance(payload.get('metadata'),dict) else {}
    session=payload.get('conversation_id') or payload.get('session_id') or metadata.get('conversation_id')
    session=str(session).strip() if session else None
    limit=state.get('max_account_concurrency',2)
    try:
        while True:
            ready,positions=await wb.routing_accounts(model,tried,limit,session)
            if not ready:
                if last is not None:return await emit_events(last,send)
                if last_error:raise last_error
                rejected.update(await wb.unavailable_reasons(model,tried,limit))
                if rejected:
                    raise HTTPException(503,{'code':'account_unavailable','reasons':dict(rejected),
                        'message':'WorkBuddy 候选账号暂不可用：'+'；'.join(f'{REASONS[code]}（{count} 个）' for code,count in rejected.items())})
                raise HTTPException(503,'WorkBuddy 此模型暂无可用账号：请查看路由计划中的冷却、额度和并发状态')
            account=ready[0];aid=account['id'];tried.add(aid)
            wb.route_cursor=positions[aid]+1
            state['account_route_id']=aid
            wb.active[aid]=wb.active.get(aid,0)+1
            async def forward(emit):
                return await dispatch_once(api,'workbuddy',scope,receive,emit,body)
            try:
                held=await hold_rate_limit(forward,send,scope['path'])
                if held is None:
                    if state.get('last_observation',{}).get('ok'):
                        wb.sunk_until.pop(aid,None)
                        runtime=getattr(wb,'runtime',None)
                        if session and runtime and runtime.proxy:runtime.proxy.POOL.affinity.bind(session,aid)
                    return
                last=held
            except HTTPException as exc:
                code=exc.detail.get('code') if isinstance(exc.detail,dict) else None
                if exc.status_code==503 and code in REASONS and not getattr(exc,'account_response_started',False):
                    rejected[code]+=1
                    api.inventory_cache=None
                    continue
                if exc.status_code!=429 or getattr(exc,'account_response_started',False):raise
                last_error=exc
            finally:
                wb.active[aid]=max(0,wb.active.get(aid,0)-1)
            wb.record_route_limit(aid,model,account.get('remaining'))
            api.inventory_cache=None
    finally:
        state.pop('account_route_id',None)


async def dispatch_once(api,provider,scope,receive,send,body):
    state = scope.setdefault('state',{})
    if provider=='codebuddy': state['account_model']=json.loads(body).get('model','')
    state.pop('selected_account',None)
    trace = CURRENT_TRACE.get()
    parsed = json.loads(body)
    full_model = provider+'/'+parsed.get('model','')
    span = trace.begin(full_model) if trace else None
    if provider=='workbuddy' and state.get('account_route_id'):
        note_account(provider,state['account_route_id'])
    observation = ResponseObservation(scope['path'])
    if trace:
        trace.current_response = observation
    async def observe(message):
        observation.feed(message)
        account = state.get('selected_account')
        if account:
            note_account(provider,account['id'],account.get('name',''))
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
        if provider=='antigravity':
            return await api.antigravity.relay(scope['path'],body,scope,receive,emit)
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
        if network and provider in {'trae', 'monkeycode', 'codebuddy', 'workbuddy', 'qoder', 'qoder-cn','antigravity'}:
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
    adaptive = isinstance(api.routes,AdaptiveRoutes)
    snapshot = await inventory(api) if adaptive else {}
    by_model = {target:model_inventory(snapshot,target) for target in route['targets']}
    supported = {}
    capabilities = getattr(api,'capabilities',None)
    if capabilities and scope['path']!='/v1/responses/compact':
        protocol = {'/v1/chat/completions':'chat','/v1/responses':'responses','/v1/messages':'messages'}[scope['path']]
        supported = {target:capabilities.describe({'id':target})['protocols'][protocol]!='unsupported' for target in route['targets']}
    if scope['path']=='/v1/responses/compact':
        supported = {target:api.capabilities.describe({'id':target}).get('compact') is True for target in route['targets']}
    if adaptive:
        route,targets = api.routes.candidates(rid,by_model,supported=supported)
    else:
        route,targets = api.routes.candidates(rid)
        targets = [target for target in targets if supported.get(target,True)]
    if not targets:
        plan = api.routes.explain(rid,by_model,supported) if adaptive else {'items':[]}
        reasons = dict(Counter(item['state'] for item in plan['items']))
        labels = {'cooldown':'渠道冷却中','unavailable':'账号池暂不可用',
                  'concurrency':'并发已满','protocol':'不支持当前接口'}
        summary = '，'.join(f'{labels.get(key,key)} {count} 个' for key,count in reasons.items())
        waits = [max(1,int(item['cooldown_until']-api.routes.clock())+1)
                 for item in plan['items'] if item['state']=='cooldown']
        retry_after = min(waits) if waits else 10
        raise HTTPException(503,{'code':'route_temporarily_unavailable','reasons':reasons,
            'retry_after':retry_after,'message':'路由暂时无可用渠道：'+(summary or '请检查目标配置')+
            f'。建议 {retry_after} 秒后重试。'})
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
        child = {**scope,'unified_route_target':target,
                 'headers':[(k,v) for k,v in scope['headers'] if k.lower()!=b'content-length']}
        child['headers'].append((b'content-length',str(len(body)).encode()))
        started = time.monotonic()
        status,ok,error_category,cancelled = 502,False,None,False
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
                            elif event['type']=='http.response.body':
                                if prefix.error or prefix.produced or prefix.terminal or prefix.completed:
                                    break
                                if sum(len(item.get('body',b'')) for item in pending)>65536:
                                    raise HTTPException(502,'上游未开始有效响应')
                        from .server import response_failed
                        response_body = b''.join(event.get('body',b'') for event in pending)
                        ok = 200<=status<300 and not response_failed(response_body[:65536])
                        error_category = prefix.error_info.get('error_category')
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
                    from .error_diagnosis import classify_error
                    if not ok:
                        error_category = classify_error(value[:65536].decode('utf-8','replace'),status).get('error_category')
                    if ok or index==len(targets)-1:
                        scope['state']['resolved_model'] = target
                        for event in events:
                            await send(event)
                        return
        except asyncio.CancelledError:
            cancelled = True
            raise
        except HTTPException as exc:
            status = exc.status_code
            from .error_diagnosis import classify_error
            error_category = classify_error(exc.detail,status).get('error_category')
            if index==len(targets)-1:
                raise
        finally:
            observation = scope['state'].get('last_observation',{})
            args = (target,ok,status,(time.monotonic()-started)*1000,route['cooldown_seconds'])
            if not cancelled:
                if adaptive:
                    api.routes.record(*args,observation.get('ttft_ms'),
                        error_category=observation.get('error_category') or error_category,
                        outcome=observation.get('outcome'))
                else:
                    api.routes.record(*args)
    raise HTTPException(503,'所有路由目标均不可用')
