"""Prefer real expiring credit packages, never expiring login credentials."""
import asyncio
from datetime import datetime, timedelta, timezone
import math
import time
from fastapi import HTTPException

DEFAULTS={'enabled':True,'window_days':7,'notify_expiring':True}


def amount(value):
    try: value=float(value)
    except (TypeError,ValueError): return None
    return value if math.isfinite(value) and value>=0 else None


def deadline(value):
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        return value/1000 if value>10**12 else value
    if not isinstance(value,str): return None
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone(timedelta(hours=8)))).timestamp()
    except ValueError: return None


class CreditPolicy:
    def __init__(self,config,clock=time.time): self.config,self.clock=config,clock

    def settings(self): return {**DEFAULTS,**self.config.get('credit_policy',{})}

    def update(self,body):
        if not isinstance(body,dict) or set(body)-set(DEFAULTS): raise HTTPException(400,'临期额度设置包含无效字段')
        result={**self.settings(),**body}
        if any(type(result[k]) is not bool for k in ('enabled','notify_expiring')) or type(result['window_days']) is not int or not 1<=result['window_days']<=90:
            raise HTTPException(400,'临期天数需要 1–90 的整数，开关需要布尔值')
        self.config.set('credit_policy',result);return result

    def summarize(self,packages,fetched_at=None,store_packages=False):
        window=self.settings()['window_days']*86400;expires=[];total=0;count=0;normalized=[]
        for p in packages if isinstance(packages,list) else []:
            if not isinstance(p,dict) or p.get('no_expiry') or p.get('is_expired') or p.get('package_code')=='enterprise': continue
            remain=amount(p.get('remain',p.get('CycleCapacityRemainPrecise',p.get('CycleCapacityRemain',p.get('CapacityRemainPrecise',p.get('CapacityRemain'))))))
            until=deadline(p.get('expire_time',p.get('PackageEndTime')))
            if not remain or until is None or not math.isfinite(until) or not self.clock()<until<self.clock()+10*365*86400: continue
            expires.append(until)
            if store_packages: normalized.append({'remain':remain,'expire_time':until})
            if until<=self.clock()+window: total+=remain;count+=1
        result={'credit_expires_at':min(expires) if expires else None,'expiring_credits':round(total,6),
                'expiring_packages':count,'credit_fetched_at':fetched_at if fetched_at is not None else self.clock()}
        if store_packages: result['credit_packages']=normalized
        return result

    def priority(self,row):
        if isinstance(row.get('credit_packages'),list): row=self.summarize(row['credit_packages'],row.get('credit_fetched_at',0))
        until=amount(row.get('credit_expires_at'))
        fetched=amount(row.get('credit_fetched_at'))
        if self.settings()['enabled'] and until and fetched is not None and 0<=self.clock()-fetched<=86400 and self.clock()<until<=self.clock()+self.settings()['window_days']*86400:
            return 0,until
        return 1,float('inf')

    async def expiring(self,store,workbuddy):
        rows=[]
        with store.lock:
            for aid,status in store.data.get('account_status',{}).items():
                account=store.data.get('accounts',{}).get(aid)
                summary=self.summarize(status['credit_packages'],status.get('credit_fetched_at',0)) if isinstance(status.get('credit_packages'),list) else status
                if account and summary.get('expiring_credits',0)>0 and self.clock()<(summary.get('credit_expires_at') or 0)<=self.clock()+self.settings()['window_days']*86400:
                    rows.append({'id':aid,'name':account['name'],'provider':'codebuddy',**{k:summary.get(k) for k in ('credit_expires_at','expiring_credits','credit_fetched_at')},'enabled':account.get('enabled',True)})
        unavailable=[]
        try:
            for account in await asyncio.wait_for(workbuddy.accounts(),4):
                credits=account.get('credits') or {}
                summary=self.summarize(credits.get('packages',[]),credits.get('fetched_at',credits.get('updated_at',0)))
                if summary['expiring_credits']:
                    rows.append({'id':account['uid'],'name':account.get('nickname') or account['uid'],'provider':'workbuddy',
                                 'enabled':account.get('enabled',False),**summary})
        except (HTTPException,asyncio.TimeoutError): unavailable.append('workbuddy')
        rows.sort(key=lambda r:r['credit_expires_at'])
        for row in rows:
            row['days_left']=round((row['credit_expires_at']-self.clock())/86400,2)
            row['stale']=self.clock()-(row.get('credit_fetched_at') or 0)>86400
            row['preferred']=row['enabled'] and not row['stale'] and self.priority(row)[0]==0
        return {'items':rows,'unavailable_providers':unavailable,'supported_providers':['codebuddy','workbuddy'],
                'note':'仅依据上游额度包的真实到期时间；登录凭据有效期不参与额度调度。'}

    async def loop(self,store,workbuddy,notify):
        while True:
            try:
                settings=self.settings()
                day=datetime.fromtimestamp(self.clock(),timezone(timedelta(hours=8))).date().isoformat()
                if settings['notify_expiring'] and self.config.get('credit_notice_day')!=day:
                    result=await self.expiring(store,workbuddy)
                    items=[r for r in result['items'] if r['enabled'] and not r['stale']]
                    if items and await notify(items): self.config.set('credit_notice_day',day)
            except asyncio.CancelledError: raise
            except Exception: pass
            await asyncio.sleep(300)
