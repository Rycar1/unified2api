"""Bounded response observations and metadata-only request diagnostics."""
from contextlib import closing
from contextvars import ContextVar
import json
import math
import time
import uuid

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from admin.server import digest
from .features import RequestLog, _usage_from
from .error_diagnosis import classify_error, diagnosis

CURRENT_TRACE = ContextVar('unified_request_trace',default=None)
ATTEMPT_FIELDS = {'kind','model','provider','account_id','account_name','status','outcome',
                  'duration_ms','ttft_ms','finish_reason','upstream_request_id','prompt_tokens','completion_tokens',
                  'error_category','upstream_code'}


def note_account(provider, account_id, name=''):
    from .account_target import confirm_account
    confirm_account(provider,account_id)
    trace = CURRENT_TRACE.get()
    if trace and trace.attempts:
        trace.attempts[-1].update(provider=provider,account_id=str(account_id)[:80],account_name=str(name)[:60])


class ResponseObservation:
    def __init__(self,path,clock=time.monotonic):
        self.path,self.clock,self.started = path,clock,clock()
        self.status,self.ttft_ms,self.finish_reason = None,None,None
        self.stream,self.completed,self.error,self.terminal,self.produced = False,False,False,False,False
        self.done = False
        self.buffer = bytearray()
        self.usage = {}
        self.upstream_request_id = None
        self.error_info = {}

    def value(self,event):
        if not isinstance(event,dict):
            return
        usage = event.get('usage') or (event.get('response') or {}).get('usage') if isinstance(event.get('response'),dict) else event.get('usage')
        if not isinstance(usage,dict) and isinstance(event.get('message'),dict): usage=event['message'].get('usage')
        if isinstance(usage,dict) and any(type(usage.get(key)) is int for key in ('prompt_tokens','completion_tokens','input_tokens','output_tokens')):
            counts=_usage_from({'usage':usage},anthropic=self.path=='/v1/messages')
            for field,aliases in {'prompt_tokens':('prompt_tokens','input_tokens'),
                                  'completion_tokens':('completion_tokens','output_tokens')}.items():
                if not any(alias in usage for alias in aliases): counts.pop(field,None)
            self.usage.update(counts)
            details=usage.get('prompt_tokens_details') or usage.get('input_tokens_details') or {}
            if not isinstance(details,dict): details={}
            cached=usage.get('prompt_cache_hit_tokens',usage.get('cache_read_input_tokens',details.get('cached_tokens')))
            if type(cached) is int and 0<=cached<=10**12: self.usage['cached_tokens']=cached
            credit=usage.get('credit')
            if type(credit) in (int,float) and math.isfinite(credit) and credit>=0: self.usage['credits']=credit
        error = event.get('error') or event.get('type') in {'error','response.failed','response.incomplete'}
        if error:
            self.error = True
        if error or self.status is not None and self.status>=400:
            found=classify_error(event,self.status)
            if found.get('upstream_code') or not self.error_info: self.error_info=found
        for choice in event.get('choices',[]) if isinstance(event.get('choices',[]),list) else []:
            if not isinstance(choice,dict):
                continue
            content = choice.get('delta') or choice.get('message') or {}
            if isinstance(content,dict) and any(content.get(key) for key in ('content','reasoning_content','tool_calls')):
                self.mark_output()
            if choice.get('finish_reason'):
                self.terminal,self.finish_reason = True,str(choice['finish_reason'])[:40]
        kind = event.get('type','')
        if kind in {'response.output_text.delta','response.reasoning_summary_text.delta','response.function_call_arguments.delta','content_block_delta'}:
            self.mark_output()
        if kind in {'response.completed','message_stop'}:
            self.terminal,self.finish_reason = True,'completed' if kind=='response.completed' else 'message_stop'
        if isinstance(event.get('output'),list) and event['output'] or isinstance(event.get('content'),list) and event['content']:
            self.mark_output()
        if event.get('status') in {'failed','incomplete'} or isinstance(event.get('response'),dict) and event['response'].get('status') in {'failed','incomplete'}:
            self.error = True
        if not self.stream and self.status and 200<=self.status<300:
            self.terminal = True

    def mark_output(self):
        self.produced = True
        if self.ttft_ms is None:
            self.ttft_ms = round((self.clock()-self.started)*1000,2)

    def feed(self,message):
        if message['type']=='http.response.start':
            self.status = message['status']
            headers = dict(message.get('headers',[]))
            self.stream = b'text/event-stream' in headers.get(b'content-type',b'')
        elif message['type']=='http.response.body':
            blob = message.get('body',b'')
            if self.stream:
                self.buffer.extend(blob)
                while b'\n' in self.buffer:
                    line,_,rest = self.buffer.partition(b'\n')
                    self.buffer = bytearray(rest)
                    if self.done:
                        continue
                    if line.startswith(b'event:') and line[6:].strip()==b'error':
                        self.error = True
                    if line.startswith(b'data:'):
                        if line[5:].strip()==b'[DONE]':
                            self.done = True
                            continue
                        try:
                            self.value(json.loads(line[5:].strip()))
                        except (ValueError,UnicodeDecodeError):
                            pass
                if len(self.buffer)>65536:
                    self.buffer.clear()
            elif len(self.buffer)<2*1024*1024:
                self.buffer.extend(blob[:2*1024*1024-len(self.buffer)])
            if not message.get('more_body',False):
                self.completed = True

    def finish(self):
        if not self.stream:
            try:
                self.value(json.loads(self.buffer))
            except (ValueError,UnicodeDecodeError):
                pass
        ok = self.status is not None and 200<=self.status<300 and self.completed and not self.error and (not self.stream or self.terminal)
        outcome = ('interrupted' if not self.completed else 'http_error' if self.status is None or not 200<=self.status<300 else
                   'stream_error' if self.error else 'missing_completion' if self.stream and not self.terminal else 'success')
        return {'status':self.status,'ok':ok,'outcome':outcome,'finish_reason':self.finish_reason,
                'ttft_ms':self.ttft_ms,'duration_ms':round((self.clock()-self.started)*1000,2),**self.error_info}


class Trace:
    def __init__(self,request_id,key_id,model,path='/v1/chat/completions'):
        self.request_id,self.key_id,self.model = request_id,key_id,model
        self.attempts,self.smart,self.max_account_concurrency = [],False,2
        self.current_response = None
        self.response = ResponseObservation(path)

    def begin(self,model):
        item = {'kind':'provider','model':model,'provider':model.partition('/')[0]}
        self.attempts.append(item)
        return item

    def billed_usage(self):
        result = dict(self.response.usage)
        if not any(type(result.get(key)) is int for key in ('prompt_tokens','completion_tokens')):
            last = self.attempts[-1] if self.attempts else {}
            for key in ('prompt_tokens','completion_tokens'):
                if type(last.get(key)) is int:
                    result[key] = max(0,last[key])
            if self.response.produced and not any(type(result.get(key)) is int for key in ('prompt_tokens','completion_tokens')):
                result['_unknown_output'] = True
        for attempt in self.attempts[:-1]:
            for key in ('prompt_tokens','completion_tokens','cached_tokens','credits'):
                value = attempt.get(key)
                if type(value) is int or key=='credits' and type(value) is float and math.isfinite(value):
                    result[key] = result.get(key,0)+max(0,value)
        billed=[self.response.usage,*self.attempts[:-1]]
        for field in ('cached_tokens','credits'):
            if any((item.get('prompt_tokens') or item.get('completion_tokens')) and field not in item for item in billed):
                result.pop(field,None)
        return result


class DiagnosticLog(RequestLog):
    def __init__(self,path,retention_days=365):
        super().__init__(path,retention_days)
        with closing(self.connect()) as db:
            present = {row[1] for row in db.execute('PRAGMA table_info(requests)')}
            for field,kind in {'request_id':'TEXT','key_id':'TEXT','ttft_ms':'REAL','finish_reason':'TEXT','attempts':'TEXT',
                               'cached_tokens':'INTEGER','credits':'REAL','cost_cny':'REAL','cost_source':'TEXT',
                               'tokens_per_second':'REAL','error_category':'TEXT','upstream_code':'TEXT'}.items():
                if field not in present:
                    db.execute('ALTER TABLE requests ADD COLUMN '+field+' '+kind)
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_request_trace ON requests(request_id)')
            db.commit()

    def record(self,row,trace=None):
        trace = trace or CURRENT_TRACE.get()
        if trace and trace.response.status is not None:
            final = trace.response.finish()
            row = {**row,'ok':final['ok'],'outcome':final['outcome'],**trace.billed_usage()}
        from .performance import price_usage
        from .performance import price_attempts
        priced=price_attempts(trace.attempts) if trace and any(item.get('prompt_tokens') or item.get('completion_tokens') for item in trace.attempts) else price_usage(row)
        generation=max(0,float(row.get('duration_ms',0))-(trace.response.ttft_ms or 0)) if trace else 0
        rate=round(row.get('completion_tokens',0)*1000/generation,2) if trace and trace.response.ttft_ms is not None and generation>0 else None
        attempts = [{key:value for key,value in item.items() if key in ATTEMPT_FIELDS and
                     (value is None or isinstance(value,(int,float,bool)) or isinstance(value,str) and len(value)<=200)}
                    for item in (trace.attempts if trace else [])[:40]]
        fields = ['created','path','model','resolved_model','provider','status','ok','duration_ms','prompt_tokens',
                  'completion_tokens','reasoning_tokens','outcome','request_id','key_id','ttft_ms','finish_reason','attempts',
                  'cached_tokens','credits','cost_cny','cost_source','tokens_per_second','error_category','upstream_code']
        model = str(row.get('model') or '')[:200]
        values = [int(time.time()),row['path'],model,row.get('resolved_model'),model.partition('/')[0],row.get('status'),
                  int(row['ok']),round(row['duration_ms']),row.get('prompt_tokens',0),row.get('completion_tokens',0),
                  row.get('reasoning_tokens',0),row['outcome'],trace.request_id if trace else None,trace.key_id if trace else None,
                  trace.response.ttft_ms if trace else None,trace.response.finish_reason if trace else None,json.dumps(attempts,separators=(',',':')),
                  row.get('cached_tokens'),row.get('credits'),priced.get('cny'),priced.get('source'),rate,
                  trace.response.error_info.get('error_category') if trace else row.get('error_category'),
                  trace.response.error_info.get('upstream_code') if trace else row.get('upstream_code')]
        with self.lock,closing(self.connect()) as db:
            db.execute('INSERT INTO requests('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',values)
            db.execute('DELETE FROM requests WHERE created<?',(int(time.time())-self.retention_days*86400,))
            db.commit()

    def detail(self,request_id):
        with self.lock,closing(self.connect()) as db:
            db.row_factory = __import__('sqlite3').Row
            row = db.execute('SELECT * FROM requests WHERE request_id=?',(request_id,)).fetchone()
        if not row:
            raise HTTPException(404,'请求记录不存在或已过保留期')
        value = dict(row)
        value['attempts'] = json.loads(value.get('attempts') or '[]')
        value['diagnosis'] = diagnosis(value)
        for attempt in value['attempts']: attempt['diagnosis']=diagnosis(attempt)
        return value

    def query(self,limit=100,offset=0,model='',ok=None):
        import sqlite3
        try:
            limit,offset = min(500,max(1,int(limit))),max(0,int(offset))
        except (ValueError,TypeError):
            limit,offset = 100,0
        clauses,args = [],[]
        if model:
            clauses.append('(model LIKE ? OR resolved_model LIKE ? OR request_id LIKE ?)')
            args.extend(['%'+str(model)[:200]+'%']*3)
        if ok in (True,False):
            clauses.append('ok=?'); args.append(int(ok))
        where = ' WHERE '+' AND '.join(clauses) if clauses else ''
        with self.lock,closing(self.connect()) as db:
            db.row_factory = sqlite3.Row
            total = db.execute('SELECT COUNT(*) FROM requests'+where,args).fetchone()[0]
            rows = db.execute('SELECT * FROM requests'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
        items = []
        for row in rows:
            value = dict(row)
            attempts = json.loads(value.pop('attempts',None) or '[]')
            account = attempts[-1] if attempts else {}
            value.update(account_id=account.get('account_id'),account_name=account.get('account_name'),attempt_count=len(attempts))
            value['diagnosis'] = diagnosis(value)
            items.append(value)
        return {'total':total,'items':items}


class TrafficMiddleware:
    def __init__(self,app,store,controls,log):
        self.app,self.store,self.controls,self.log = app,store,controls,log

    async def __call__(self,scope,receive,send):
        if scope.get('type')!='http' or scope.get('method')!='POST' or scope.get('path') not in {'/v1/chat/completions','/v1/responses','/v1/responses/compact','/v1/messages'}:
            return await self.app(scope,receive,send)
        request_id = str(uuid.uuid4())
        headers = dict(scope.get('headers',[]))
        authorization = headers.get(b'authorization',b'').decode('latin1')
        token = authorization[7:].strip() if authorization.startswith('Bearer ') else headers.get(b'x-api-key',b'').decode('latin1')
        kid = None
        parts,size = [],0
        while True:
            message = await receive()
            if message['type']=='http.disconnect':
                return
            size += len(message.get('body',b''))
            if size<=8*1024*1024:
                parts.append(message.get('body',b''))
            if not message.get('more_body',False):
                break
        body = b''.join(parts)
        try:
            payload = json.loads(body)
        except (ValueError,UnicodeDecodeError):
            payload = {}
        payload = payload if isinstance(payload,dict) else {}
        trace = Trace(request_id,kid,str(payload.get('model') or '')[:200],scope['path'])
        scope.setdefault('state',{})['trace'] = trace
        trace_token = CURRENT_TRACE.set(trace)
        lease = None
        replayed = False
        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {'type':'http.request','body':body,'more_body':False}
            return await receive()
        async def observe(message):
            if message['type']=='http.response.start':
                message = {**message,'headers':list(message.get('headers',[]))+[(b'x-request-id',request_id.encode())]}
            trace.response.feed(message)
            await send(message)
        try:
            if size>8*1024*1024:
                raise HTTPException(413,'请求超过 8 MB')
            self.store.check_api(authorization,headers.get(b'x-api-key',b'').decode('latin1'))
            with self.store.lock:
                kid = next((key for key,item in self.store.data['keys'].items() if __import__('hmac').compare_digest(item['hash'],digest(token))),None)
            trace.key_id = kid
            lease = self.controls.admit(kid,payload,request_id)
            await self.app(scope,replay,observe)
        except HTTPException as exc:
            if trace.response.status is not None:
                raise
            await JSONResponse({'error':{'message':exc.detail,'type':'rate_limit_error' if exc.status_code==429 else 'invalid_request_error'},'request_id':request_id},exc.status_code)(scope,replay,observe)
            self.log.record({'path':scope['path'],'model':trace.model,'status':exc.status_code,'ok':False,'duration_ms':0,'outcome':'rejected'},trace)
        finally:
            trace.response.finish()
            self.controls.finish(lease,trace.billed_usage(),produced=trace.response.produced)
            CURRENT_TRACE.reset(trace_token)
