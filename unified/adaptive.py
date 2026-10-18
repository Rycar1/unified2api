"""Availability-aware channel selection with atomic concurrency leases."""
from collections import defaultdict
from contextlib import contextmanager
import math
from fastapi import HTTPException
from .features import RouteManager


class AdaptiveRoutes(RouteManager):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.active = defaultdict(int)
        changed = False
        for item in self.health.values():
            if item.get('last_status') in {200,201,401,402,403,408,409,429,500,502,503,504} and item.get('last_error_category')!='model_not_found':
                until = item.get('cooldown_until',0)
                if until>self.clock()+10:
                    item['cooldown_until'] = int(self.clock())+10
                    changed = True
        if changed:
            self.config.set('route_health',self.health)

    def validate(self,value,existing_models,current_id=None):
        smart = isinstance(value,dict) and value.get('strategy')=='smart'
        route = super().validate({**value,'strategy':'priority'} if smart else value,existing_models,current_id)
        if smart:
            route['strategy'] = 'smart'
        for key,default,maximum in [('max_concurrency',8,1000),('max_account_concurrency',2,100)]:
            number = value.get(key,default)
            if type(number) is not int or not 1<=number<=maximum:
                raise HTTPException(400,'路由并发上限必须是有效正整数')
            route[key] = number
        return route

    def candidates(self,rid,inventory=None,peek=False,supported=None):
        route = self.get(rid)
        inventory = inventory or {}
        supported = supported or {}
        with self.lock:
            ready = [model for model in route['targets'] if self.health.get(model,{}).get('cooldown_until',0)<=self.clock()
                     and supported.get(model,True) and inventory.get(model,{}).get('available') is not False
                     and (route['strategy']!='smart' or self.active[model]<route.get('max_concurrency',8))]
            def score(model):
                health = self.health.get(model,{})
                recent = health.get('recent',[])
                reliability = (sum(recent)+2)/(len(recent)+2)
                elapsed = health.get('avg_ttft_ms',health.get('avg_duration_ms',1000))
                elapsed = elapsed if isinstance(elapsed,(int,float)) and math.isfinite(elapsed) else 1000
                # Balances only gate eligibility; different platforms use different units.
                return (1+self.active[model]+inventory.get(model,{}).get('active',0))*(1+max(0,elapsed)/1000)/reliability
            if route['strategy']=='smart':
                ready.sort(key=score)
            elif route['strategy']=='round_robin' and ready:
                offset = self.cursors[rid] % len(ready)
                if not peek:
                    self.cursors[rid] += 1
                ready = ready[offset:] + ready[:offset]
            elif route['strategy']=='latency':
                ready.sort(key=lambda model:self.health.get(model,{}).get('avg_duration_ms',float('inf')))
        return route,ready[:route['retries']+1]

    def explain(self, rid, inventory, supported=None):
        route, targets = self.candidates(rid, inventory, peek=True, supported=supported)
        supported = supported or {}
        targets = [t for t in targets if supported.get(t, True)]
        selected = targets[0] if targets else None
        items = []
        for target in route['targets']:
            health = self.health.get(target, {})
            snapshot = inventory.get(target, {})
            state, reason = 'standby', '备用目标'
            if not supported.get(target, True):
                state, reason = 'protocol', '不支持选定的接口协议'
            elif snapshot.get('available') is False:
                state, reason = 'unavailable', '没有余额和状态均可用的账号'
            elif route['strategy'] == 'smart' and self.active[target] >= route['max_concurrency']:
                state, reason = 'concurrency', '当前目标并发已满'
            elif health.get('cooldown_until', 0) > self.clock():
                state, reason = ('fallback', '全部目标冷却，按现有策略再试一次') if target in targets else ('cooldown', '失败冷却中')
            elif target == selected:
                state, reason = 'selected', '下次请求优先尝试此目标'
            elif target not in targets:
                state, reason = 'retry_limit', '超出本次最大尝试次数'
            items.append({'model': target, 'state': state, 'reason': reason,
                          'position': targets.index(target) + 1 if target in targets else None,
                          'active': self.active[target], 'cooldown_until': health.get('cooldown_until', 0),
                          'last_status': health.get('last_status'), 'accounts': snapshot.get('candidates', [])})
        return {'id': rid, 'strategy': route['strategy'], 'selected': selected, 'items': items}

    @contextmanager
    def reserve(self,model,route):
        with self.lock:
            if route['strategy']=='smart' and self.active[model]>=route.get('max_concurrency',8):
                raise HTTPException(429,'该目标的并发已达上限')
            self.active[model] += 1
        try:
            yield
        finally:
            with self.lock:
                self.active[model] = max(0,self.active[model]-1)

    def record(self,target,ok,status,duration_ms,cooldown_seconds,ttft_ms=None,error_category=None,outcome=None):
        # An incomplete transport observation does not establish an upstream
        # fault. Keep its request log, but do not poison channel eligibility.
        if not ok and outcome=='interrupted':
            return
        if not ok and error_category!='model_not_found':
            # Pools already cool/disable individual failing accounts. A single
            # account's throttle must not lock all other funded accounts out.
            cooldown_seconds = min(cooldown_seconds,10)
        with self.lock:
            health = self.health.setdefault(target,{})
            health['last_error_category'] = error_category if not ok else None
            health['last_outcome'] = outcome
            health['recent'] = (health.get('recent',[])+[bool(ok)])[-20:]
            # Absence/denied quota can be temporary and specific to this model.
            # Parameter errors belong to the request and must not disable it.
            if not ok and error_category in {'model_not_found','quota_exhausted'}:
                health['cooldown_until'] = int(self.clock()) + cooldown_seconds
            if ok and ttft_ms is not None:
                old = health.get('avg_ttft_ms')
                health['avg_ttft_ms'] = round(ttft_ms if old is None else old*.8+ttft_ms*.2)
        return super().record(target,ok,status,duration_ms,cooldown_seconds)


def availability(rows):
    """Normalize account snapshots without treating missing balances as exhausted."""
    ready = [row for row in rows if row.get('enabled',True) and row.get('status') not in
             {'paused','invalid','expired','cooling','exhausted'} and row.get('pool_state') not in
             {'paused','invalid','cooling','exhausted'} and not row.get('disabled') and not row.get('cooling')]
    ready = [row for row in ready if row.get('remaining')!=0 or row.get('credits_stale') is True]
    candidates = [{'id':row.get('id',row.get('uid','')), 'name':row.get('name') or row.get('nickname') or row.get('uid',''),
                   'status':row.get('status',row.get('pool_state','unknown')), 'enabled':row.get('enabled',True),
                   'available':row in ready, 'remaining':row.get('remaining'),
                   'active':row.get('inflight',row.get('in_flight',0))} for row in rows]
    return {'available':bool(ready),'accounts':len(ready),'active':sum(row.get('inflight',row.get('in_flight',0)) for row in ready),
            'candidates':candidates}
