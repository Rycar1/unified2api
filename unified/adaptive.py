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

    def candidates(self,rid,inventory=None,peek=False):
        route = self.get(rid)
        if route['strategy']!='smart':
            return super().candidates(rid,peek=peek)
        inventory = inventory or {}
        with self.lock:
            ready = [model for model in route['targets'] if self.health.get(model,{}).get('cooldown_until',0)<=self.clock()
                     and self.active[model]<route.get('max_concurrency',8) and inventory.get(model,{}).get('available') is not False]
            def score(model):
                health = self.health.get(model,{})
                recent = health.get('recent',[])
                reliability = (sum(recent)+2)/(len(recent)+2)
                elapsed = health.get('avg_ttft_ms',health.get('avg_duration_ms',1000))
                elapsed = elapsed if isinstance(elapsed,(int,float)) and math.isfinite(elapsed) else 1000
                # Balances only gate eligibility; different platforms use different units.
                return (1+self.active[model]+inventory.get(model,{}).get('active',0))*(1+max(0,elapsed)/1000)/reliability
            ready.sort(key=score)
        return route,ready[:route['retries']+1]

    def explain(self, rid, inventory, supported=None):
        route, targets = self.candidates(rid, inventory, peek=True)
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
            elif route['strategy'] == 'smart' and snapshot.get('available') is False:
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

    def record(self,target,ok,status,duration_ms,cooldown_seconds,ttft_ms=None):
        with self.lock:
            health = self.health.setdefault(target,{})
            health['recent'] = (health.get('recent',[])+[bool(ok)])[-20:]
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
