"""Drive only a newly created daily activity session; persist counters, never text."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
import importlib
from pathlib import Path
import sys
from urllib.parse import quote, urlsplit
from fastapi import HTTPException
from admin.server import write_json
from .errors import safe_error_message

ORIGIN='https://www.workbuddy.ai'
BASE=ORIGIN+'/console/as/conversations/'
USER_AGENT='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36'

def guard_sandbox(link):
    source=str(Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub')
    if source not in sys.path: sys.path.insert(0,source)
    importlib.import_module('wb_webtools')._guard_url(link,resolve=True)


class ActivityConfig:
    def __init__(self,path): self.path=path; self.lock=threading.RLock()
    def get(self,key,default=None):
        with self.lock:
            try: return json.loads(self.path.read_text(encoding='utf-8')).get(key,default)
            except (OSError,ValueError): return default
    def set(self,key,value):
        with self.lock:
            try: data=json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError,ValueError): data={}
            data[key]=value; write_json(self.path,data)


class WebActivity:
    def __init__(self,config,runner=None):
        self.config,self.runner=config,runner
        self.locks={}; self.state_lock=threading.RLock()

    def record(self,account,result):
        lock=getattr(getattr(self.config,'store',None),'lock',self.state_lock)
        with lock:
            history=self.config.get('activity_history',[])
            row={'provider':'workbuddy_intl','account_id':account.uid,'action':'daily_activity',
                'created':int(time.time()),**{key:result.get(key) for key in ('ok','state','remaining','earned_credit','message')}}
            self.config.set('activity_history',(history+[row])[-1000:])
        result['_history_recorded']=True
        return result

    def request(self,account,path,body=None):
        import wb_accounts
        import urllib.request
        headers={'Authorization':'Bearer '+account.access_token,'X-User-Id':account.uid,
            'Content-Type':'application/json','Accept':'application/json','Origin':ORIGIN,
            'Referer':ORIGIN+'/app','User-Agent':USER_AGENT}
        req=urllib.request.Request(BASE+path,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        with wb_accounts.urlopen(req,timeout=30,proxy=account.proxy) as response:
            content=response.read(1024*1024+1)
        if len(content)>1024*1024: raise ValueError('Activity response too large')
        return json.loads(content)

    def run(self,account):
        if account.realm!='intl': return {'ok':False,'message':'网页活跃只适用于国际站账号'}
        with self.state_lock: lock=self.locks.setdefault(account.uid,threading.Lock())
        with lock:
            day=datetime.now(timezone(timedelta(hours=8))).date().isoformat()
            previous=self.config.get('web_activity',{}).get(account.uid,{})
            if previous.get('day')==day and previous.get('ok'):
                return {'ok':True,'already':True,'state':'already_completed','message':'今日已完成网页活跃',
                    'remaining':(account.credits or {}).get('remain'),'earned_credit':0}
            token=''
            try:
                created=self.request(account,'',{'prompt':'Hi','model':'deepseek-v4.1-flash',
                    'conversationOrigin':'workbuddy-app','plugins':[{'name':'weixinpay','marketplace':'codebuddy-builtin'}]})
                cid=(created.get('data') or {}).get('id')
                if created.get('code') not in (None,0) or not cid: raise ValueError('创建活跃会话失败')
                path=quote(str(cid),safe='')
                session=(self.request(account,path+'/session').get('data') or {})
                link=session.get('link') or session.get('endpoint') or ''
                token=session.get('token') or ''
                parsed=urlsplit(link)
                if parsed.scheme!='https' or not parsed.hostname or parsed.username or not token:
                    raise ValueError('沙箱未就绪或返回无效地址')
                if parsed.hostname=='api.zxcbug.com' or parsed.hostname.endswith('.api.zxcbug.com'):
                    raise ValueError('沙箱地址无效')
                guard_sandbox(link)
                def poll(): return str((self.request(account,path).get('data') or {}).get('status') or '')
                if self.runner is None:
                    from wb_webagent import run_turn
                    runner=run_turn
                else: runner=self.runner
                raw=runner(link,token,session.get('sessionId') or session.get('session_id') or cid,
                    session.get('cwd') or '/workspace','Hi',USER_AGENT,poll_status=poll,wait_seconds=120,
                    proxy=account.proxy,model='deepseek-v4.1-flash')
                if not raw.get('ok'): raise ValueError(raw.get('error') or '活跃会话未正常完成')
                result={'ok':True,'state':'completed','status':raw.get('status'),'chunks':raw.get('chunks',0),
                    'elapsed_ms':raw.get('elapsed_ms'),'remaining':(account.credits or {}).get('remain'),
                    'earned_credit':None,'message':'网页活跃会话已完成，奖励发放以余额明细为准'}
                with self.state_lock:
                    states=self.config.get('web_activity',{})
                    states[account.uid]={'day':day,'ok':True,'at':int(time.time()),'chunks':result['chunks']}
                    self.config.set('web_activity',states)
                account.last_daily_chat=time.strftime('%Y-%m-%d %H:%M:%S')
                if hasattr(account,'save') and getattr(account,'path',None):
                    try: account.save(str(Path(account.path).parent))
                    except OSError: result['account_state_stale']=True
                try:
                    account.fetch_credits()
                    result['remaining']=(account.credits or {}).get('remain')
                except Exception:
                    result['balance_stale']=True
                    result['message']='网页活跃已完成，余额刷新暂时失败，请稍后查询'
                return self.record(account,result)
            except Exception as exc:
                return self.record(account,{'ok':False,'state':'failed','message':safe_error_message(str(exc),502,(account.access_token,token,getattr(account,'refresh_token','')))})

    def install(self,proxy):
        import wb_accounts
        cls=wb_accounts.Account
        original=getattr(cls,'_unified_original_daily_chat',cls.daily_chat)
        cls._unified_original_daily_chat=original
        activity=self
        def daily_chat(account,*args,**kwargs):
            if account.realm!='intl': return original(account,*args,**kwargs)
            result=activity.run(account)
            result['msg']=result.get('message','')
            return result
        cls.daily_chat=daily_chat
        proxy.UNIFIED_WEB_ACTIVITY=self
