"""Durable API-key budgets, admission reservations and concurrency limits."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import threading
import time

from fastapi import HTTPException

CN = timezone(timedelta(hours=8))
DEFAULT = {'daily_tokens':0,'monthly_tokens':0,'max_concurrency':0,
           'requests_per_minute':0,'allowed_models':[],'enabled':True}


class KeyControls:
    def __init__(self, config, path, clock=time.time):
        self.config, self.path, self.clock = config, Path(path), clock
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock = threading.RLock()
        with closing(self.connect()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS key_admissions (
                request_id TEXT PRIMARY KEY, key_id TEXT NOT NULL, created REAL NOT NULL,
                day TEXT NOT NULL, month TEXT NOT NULL, reservation INTEGER NOT NULL,
                tokens INTEGER NOT NULL DEFAULT 0, estimated INTEGER NOT NULL DEFAULT 0,
                pending INTEGER NOT NULL DEFAULT 1)''')
            db.execute('CREATE INDEX IF NOT EXISTS idx_key_admission_period ON key_admissions(key_id,month,day)')
            # A crashed process cannot know how much its unfinished upstreams spent.
            db.execute('UPDATE key_admissions SET pending=0,tokens=reservation,estimated=reservation WHERE pending=1')
            db.commit()

    def connect(self):
        return sqlite3.connect(self.path,timeout=10)

    def policy(self, kid):
        return {**DEFAULT,**self.config.get('key_policies',{}).get(kid,{})}

    def save(self, kid, body):
        with self.config.store.lock:
            if kid not in self.config.store.data['keys']:
                raise HTTPException(404,'密钥不存在')
            if not isinstance(body,dict) or set(body)-set(DEFAULT):
                raise HTTPException(400,'密钥限制包含不支持的字段')
            result = self.policy(kid)
            for field,upper in [('daily_tokens',10**12),('monthly_tokens',10**12),('max_concurrency',1000),('requests_per_minute',100000)]:
                value = body.get(field,result[field])
                if type(value) is not int or not 0 <= value <= upper:
                    raise HTTPException(400,'额度、并发和频率必须是范围内的非负整数')
                result[field] = value
            enabled = body.get('enabled',result['enabled'])
            models = body.get('allowed_models',result['allowed_models'])
            if type(enabled) is not bool or not isinstance(models,list) or len(models)>2000:
                raise HTTPException(400,'启用状态或模型白名单格式不正确')
            if any(not isinstance(model,str) or not 1<=len(model)<=200 or any(c in model for c in '\r\n\0')
                   or ('/' not in model and model!='*') or any(c in model for c in '?[]')
                   or ('*' in model and model!='*' and not (model.endswith('/*') and model.count('*')==1)) for model in models):
                raise HTTPException(400,'模型需使用完整 ID 或 平台/*')
            result.update(enabled=enabled,allowed_models=list(dict.fromkeys(models)))
            policies = self.config.get('key_policies',{})
            policies[kid] = result
            self.config.set('key_policies',policies)
            return result

    def allowed(self, kid, model):
        models = self.policy(kid)['allowed_models'] if kid else []
        return not models or any(item==model or item=='*' or item.endswith('/*') and model.startswith(item[:-1]) for item in models)

    @staticmethod
    def estimate(payload):
        content = {key:payload[key] for key in ('messages','input','instructions','system','tools') if key in payload}
        # UTF-8 bytes form a conservative input estimate; include framing overhead.
        prompt = len(json.dumps(content,ensure_ascii=False).encode())+32
        output = payload.get('max_completion_tokens',payload.get('max_output_tokens',payload.get('max_tokens',4096)))
        if type(output) is not int or not 1<=output<=10**7:
            output = 4096
        return prompt+output

    def periods(self):
        date = datetime.fromtimestamp(self.clock(),CN)
        return date.strftime('%Y-%m-%d'),date.strftime('%Y-%m')

    def usage(self, kid):
        day, month = self.periods()
        with self.lock,closing(self.connect()) as db:
            values = db.execute('''SELECT
                COALESCE(SUM(CASE WHEN day=? THEN tokens ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN month=? THEN tokens ELSE 0 END),0), COALESCE(SUM(pending),0),
                COALESCE(SUM(CASE WHEN pending=1 THEN reservation ELSE 0 END),0),
                COALESCE(SUM(CASE WHEN month=? THEN estimated ELSE 0 END),0), COALESCE(SUM(created>?),0)
                FROM key_admissions WHERE key_id=? AND (month=? OR pending=1 OR created>?)''',
                (day,month,month,self.clock()-60,kid,month,self.clock()-60)).fetchone()
        return dict(zip(('day_tokens','month_tokens','active','reserved_tokens','estimated_tokens','minute_requests'),values))

    def admit(self, kid, payload, request_id):
        if not kid:
            return None
        policy = self.policy(kid)
        if not policy['enabled']:
            raise HTTPException(403,'该密钥已暂停')
        if not self.allowed(kid,str(payload.get('model') or '')):
            raise HTTPException(403,'该密钥不允许调用此模型或路由')
        day,month = self.periods()
        reservation = self.estimate(payload) if policy['daily_tokens'] or policy['monthly_tokens'] else 0
        with self.lock,closing(self.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT day,tokens,reservation,pending,created FROM key_admissions WHERE key_id=? AND (month=? OR pending=1 OR created>?)',
                              (kid,month,self.clock()-60)).fetchall()
            active = sum(row[3] for row in rows)
            rate = sum(row[4]>self.clock()-60 for row in rows)
            if policy['max_concurrency'] and active>=policy['max_concurrency']:
                raise HTTPException(429,'该密钥的并发请求数已达上限')
            if policy['requests_per_minute'] and rate>=policy['requests_per_minute']:
                raise HTTPException(429,'该密钥的每分钟请求数已达上限')
            for field,selected in [('daily_tokens',[row for row in rows if row[0]==day]),('monthly_tokens',[row for row in rows if row[0].startswith(month)])]:
                used = sum(row[1]+row[2]*row[3] for row in selected)
                if policy[field] and used+reservation>policy[field]:
                    raise HTTPException(429,'该密钥的'+('每日' if field=='daily_tokens' else '每月')+' Token 额度不足以预占本次请求；请降低输出上限或调整额度')
            db.execute('INSERT INTO key_admissions(request_id,key_id,created,day,month,reservation) VALUES(?,?,?,?,?,?)',
                       (request_id,kid,self.clock(),day,month,reservation))
            db.commit()
        return request_id

    def finish(self, lease, usage, produced=False):
        if not lease:
            return
        values = [usage.get(key) for key in ('prompt_tokens','completion_tokens')]
        reported = any(type(value) is int for value in values)
        tokens = sum(max(0,value) for value in values if type(value) is int)
        with self.lock,closing(self.connect()) as db:
            row = db.execute('SELECT reservation FROM key_admissions WHERE request_id=? AND pending=1',(lease,)).fetchone()
            if not row:
                return
            estimated = row[0] if produced and (not reported or usage.get('_unknown_output')) else 0
            db.execute('UPDATE key_admissions SET pending=0,tokens=?,estimated=? WHERE request_id=?',
                       (tokens+estimated,estimated,lease))
            db.commit()
