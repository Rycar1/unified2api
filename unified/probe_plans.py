"""Opt-in synthetic compatibility checks with durable admission budgets."""
import asyncio
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3
import time
from fastapi import HTTPException
from .error_diagnosis import classify_error, diagnosis
from .model_aliases import MODEL_ID

CHECKS = ('chat','stream','responses','messages','tools','reasoning')
DEFAULTS = {'enabled':False,'models':[],'checks':['chat','stream'],'interval_minutes':60,
            'daily_requests':20,'daily_tokens':20000,'max_tokens':256,'notify_failures':True}


class ProbePlans:
    def __init__(self,config,path,runner,clock=time.time,notify=None):
        self.config,self.path,self.runner,self.clock,self.notify=config,Path(path),runner,clock,notify
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=asyncio.Lock()
        self.task=None
        with closing(self.connect()) as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS probe_runs (
                id INTEGER PRIMARY KEY, day TEXT, model TEXT, check_name TEXT, started REAL,
                duration_ms INTEGER, status INTEGER, ok INTEGER, outcome TEXT,
                error_category TEXT, upstream_code TEXT, tokens INTEGER);
                CREATE TABLE IF NOT EXISTS probe_state (id INTEGER PRIMARY KEY, lease_until REAL, next_at REAL);
                INSERT OR IGNORE INTO probe_state VALUES(1,0,0);''')
            db.commit()

    def connect(self): return sqlite3.connect(self.path,timeout=5)

    def settings(self): return {**DEFAULTS,**self.config.get('probe_plans',{})}

    def update(self,body):
        if not isinstance(body,dict) or set(body)-set(DEFAULTS): raise HTTPException(400,'巡检设置包含无效字段')
        value={**self.settings(),**body}
        if any(type(value[k]) is not bool for k in ('enabled','notify_failures')): raise HTTPException(400,'巡检开关必须是布尔值')
        for k,lo,hi in [('interval_minutes',5,1440),('daily_requests',1,1000),('daily_tokens',512,10000000),('max_tokens',32,2048)]:
            if type(value[k]) is not int or not lo<=value[k]<=hi: raise HTTPException(400,'巡检预算、间隔或输出上限无效')
        if not isinstance(value['models'],list) or len(value['models'])>10 or any(not isinstance(m,str) or not MODEL_ID.fullmatch(m) for m in value['models']):
            raise HTTPException(400,'最多选择 10 个完整模型 ID')
        if not isinstance(value['checks'],list) or not value['checks'] or any(c not in CHECKS for c in value['checks']): raise HTTPException(400,'请选择有效的巡检项目')
        value['models']=list(dict.fromkeys(value['models']));value['checks']=list(dict.fromkeys(value['checks']))
        if value['enabled'] and not value['models']: raise HTTPException(400,'开启巡检前请先选择模型')
        self.config.set('probe_plans',value)
        return value

    def day(self): return datetime.fromtimestamp(self.clock(),timezone(timedelta(hours=8))).date().isoformat()

    def status(self):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            count,tokens=db.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM probe_runs WHERE day=?',(self.day(),)).fetchone()
            state=dict(db.execute('SELECT lease_until,next_at FROM probe_state WHERE id=1').fetchone())
            history=[dict(r) for r in db.execute('SELECT * FROM probe_runs ORDER BY id DESC LIMIT 100')]
        for row in history:
            row['check']=row.pop('check_name');row['diagnosis']=diagnosis(row)
            if row['outcome']=='running' and state['lease_until']<self.clock(): row['outcome']='interrupted';row['ok']=0
        return {'settings':self.settings(),'usage':{'requests':count,'tokens':tokens,'day':self.day()},
                'running':state['lease_until']>self.clock(),'next_at':state['next_at'] if self.settings()['enabled'] else None,'history':history}

    def _claim(self,settings,force):
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            lease,next_at=db.execute('SELECT lease_until,next_at FROM probe_state WHERE id=1').fetchone()
            if lease>self.clock() or not force and next_at>self.clock(): return False
            db.execute('UPDATE probe_state SET lease_until=?,next_at=? WHERE id=1',(self.clock()+90,self.clock()+settings['interval_minutes']*60))
            db.commit()
        return True

    def _reserve(self,model,check,body,settings):
        from .token_estimate import input_tokens
        budget=settings['max_tokens']+input_tokens(body)
        with closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            count,tokens=db.execute('SELECT COUNT(*),COALESCE(SUM(tokens),0) FROM probe_runs WHERE day=?',(self.day(),)).fetchone()
            if count>=settings['daily_requests'] or tokens+budget>settings['daily_tokens']: return None
            cursor=db.execute("INSERT INTO probe_runs(day,model,check_name,started,outcome,tokens) VALUES(?,?,?,?,?,?)",(self.day(),model,check,self.clock(),'running',budget))
            db.execute('UPDATE probe_state SET lease_until=? WHERE id=1',(self.clock()+90,))
            db.commit();return cursor.lastrowid

    @staticmethod
    def payload(model,check,settings):
        marker='unified-probe-'+secrets.token_hex(8)
        body={'model':model,'max_tokens':settings['max_tokens'],'stream':check=='stream',
              'messages':[{'role':'user','content':marker}]}
        path='/v1/chat/completions'
        if check=='responses':
            body={'model':model,'input':marker,'stream':False,'max_output_tokens':settings['max_tokens']};path='/v1/responses'
        elif check=='messages': path='/v1/messages'
        elif check=='reasoning': body['reasoning_effort']='high'
        elif check=='tools':
            body['tools']=[{'type':'function','function':{'name':'unified_probe','description':'Call with the exact supplied value.',
                'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value']}}}]
            body['tool_choice']={'type':'function','function':{'name':'unified_probe'}}
        return path,body,marker

    @staticmethod
    def assess(result,check,marker):
        status=result.get('status',502);obs=result.get('observation') or {};value=result.get('value') or {}
        info=classify_error(value,status) if status>=400 else {k:obs[k] for k in ('error_category','upstream_code') if k in obs}
        ok=200<=status<300 and not value.get('error') and obs.get('ok',True)
        choices=value.get('choices') or []
        if check=='stream': ok=ok and obs.get('stream') is True and obs.get('produced') is True
        elif check=='responses': ok=ok and value.get('object')=='response' and value.get('status')=='completed' and bool(value.get('output'))
        elif check=='messages': ok=ok and value.get('type')=='message' and bool(value.get('content')) and bool(value.get('stop_reason'))
        elif check=='tools':
            valid=False
            for choice in choices:
                for call in (choice.get('message') or {}).get('tool_calls') or []:
                    fn=call.get('function') or {}
                    try: valid|=fn.get('name')=='unified_probe' and json.loads(fn.get('arguments',''))=={'value':marker}
                    except (ValueError,TypeError): pass
            ok=ok and valid
        elif check=='reasoning':
            usage=value.get('usage') or {};details=usage.get('completion_tokens_details') or usage.get('output_tokens_details') or {}
            ok=ok and (any((c.get('message') or {}).get('reasoning_content') for c in choices) or (details.get('reasoning_tokens') or 0)>0)
        else: ok=ok and any(bool((c.get('message') or {}).get('content')) and c.get('finish_reason') for c in choices)
        outcome='success' if ok else 'http_error' if status>=400 else 'capability_unverified' if obs.get('ok',True) else 'stream_incomplete'
        return {'status':status,'ok':bool(ok),'outcome':outcome,**info}

    async def run(self,force=True):
        if self.lock.locked(): return self.status()
        async with self.lock:
            settings=self.settings()
            if not self._claim(settings,force): return self.status()
            try:
                for model in settings['models']:
                    for check in settings['checks']:
                        path,body,marker=self.payload(model,check,settings)
                        rid=self._reserve(model,check,body,settings)
                        if rid is None: return self.status()
                        started=time.monotonic()
                        with closing(self.connect()) as db:
                            previous=db.execute('SELECT ok FROM probe_runs WHERE model=? AND check_name=? AND id<? ORDER BY id DESC LIMIT 1',(model,check,rid)).fetchone()
                        try:
                            result=await asyncio.wait_for(self.runner(path,body),45)
                            record=self.assess(result,check,marker)
                            usage=result.get('usage') or {}
                            actual=sum(usage.get(k,0) for k in ('prompt_tokens','completion_tokens') if type(usage.get(k)) is int)
                        except asyncio.CancelledError:
                            record={'status':None,'ok':False,'outcome':'interrupted'};actual=0
                            self._finish(rid,record,0,round((time.monotonic()-started)*1000));raise
                        except Exception:
                            record={'status':504,'ok':False,'outcome':'timeout','error_category':'first_token_timeout'};actual=0
                        self._finish(rid,record,actual,round((time.monotonic()-started)*1000))
                        if self.notify and settings['notify_failures'] and not record['ok'] and (previous is None or previous[0]):
                            await self.notify({'model':model,'check':check,**record})
            finally:
                with closing(self.connect()) as db:
                    db.execute('UPDATE probe_state SET lease_until=0 WHERE id=1');db.commit()
            return self.status()

    def _finish(self,rid,record,actual,duration):
        with closing(self.connect()) as db:
            db.execute('UPDATE probe_runs SET status=?,ok=?,outcome=?,error_category=?,upstream_code=?,duration_ms=?,tokens=CASE WHEN ?>0 THEN ? ELSE tokens END WHERE id=?',
                       (record['status'],int(record['ok']),record['outcome'],record.get('error_category'),record.get('upstream_code'),duration,actual,actual,rid))
            db.execute('DELETE FROM probe_runs WHERE id NOT IN (SELECT id FROM probe_runs ORDER BY id DESC LIMIT 10000) AND day<>?',(self.day(),))
            db.commit()

    async def tick(self):
        if self.settings()['enabled']: await self.run(force=False)

    def start(self):
        if self.task and not self.task.done(): raise HTTPException(409,'巡检正在运行')
        if not self.settings()['models']: raise HTTPException(400,'请先选择要检测的模型')
        self.task=asyncio.create_task(self.run());return {'started':True}

    async def loop(self):
        while True:
            try: await self.tick()
            except asyncio.CancelledError: raise
            except Exception: pass
            await asyncio.sleep(30)


class ProbeRunner:
    def __init__(self,api,store): self.api,self.store=api,store

    async def __call__(self,path,body):
        from admin.server import digest
        from .diagnostics import CURRENT_TRACE,ResponseObservation,Trace
        key=secrets.token_urlsafe(32);hashed=digest(key)
        with self.store.lock: self.store.test_keys.add(hashed)
        raw=json.dumps(body).encode();parts=bytearray();obs=ResponseObservation(path)
        trace=Trace(secrets.token_hex(16),None,body['model'],path);token=CURRENT_TRACE.set(trace)
        sent=False
        async def receive():
            nonlocal sent
            if not sent: sent=True;return {'type':'http.request','body':raw,'more_body':False}
            await asyncio.Event().wait()
        async def send(message):
            obs.feed(message)
            if message['type']=='http.response.body' and not obs.stream:
                parts.extend(message.get('body',b'')[:max(0,2*1024*1024-len(parts))])
        scope={'type':'http','method':'POST','path':path,'query_string':b'','scheme':'http','server':('probe',80),
               'client':('127.0.0.1',0),'http_version':'1.1','headers':[(b'authorization',('Bearer '+key).encode()),(b'content-type',b'application/json'),(b'content-length',str(len(raw)).encode())],
               'state':{'scheduled_probe':True}}
        try:
            await self.api(scope,receive,send)
            try: value=json.loads(parts)
            except (ValueError,UnicodeDecodeError): value={}
            return {'status':obs.status or 502,'value':value if isinstance(value,dict) else {},
                    'observation':{**obs.finish(),'stream':obs.stream,'produced':obs.produced},'usage':obs.usage}
        finally:
            CURRENT_TRACE.reset(token)
            with self.store.lock: self.store.test_keys.discard(hashed)
