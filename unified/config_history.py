"""Encrypted configuration journal; public views contain redacted changes only."""
from contextlib import closing
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException

SECRET_FIELDS={'key','balancekey','apikey','token','accesstoken','refreshtoken','password','authorization','cookie','secret','webhookurl'}


def secret_field(key): return str(key).lower().replace('_','').replace('-','') in SECRET_FIELDS


def redacted(value,key=''):
    if secret_field(key): return '[已隐藏]' if value is not None else None
    if isinstance(value,dict): return {k:redacted(v,k) for k,v in value.items()}
    if isinstance(value,list): return [redacted(v) for v in value[:100]]
    if isinstance(value,str) and len(value)>300: return value[:300]+'…'
    return value


def changes(before,after,path=''):
    if before==after: return []
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(before.keys()|after.keys()):
            child=(path+'.' if path else '')+key
            if secret_field(key):
                if before.get(key)!=after.get(key): result.append({'path':child,'before':redacted(before.get(key),key),'after':redacted(after.get(key),key)})
            else: result.extend(changes(before.get(key),after.get(key),child))
        return result
    if isinstance(before,list) and isinstance(after,list) and all(isinstance(v,dict) and 'id' in v for v in before+after):
        return changes({v['id']:v for v in before},{v['id']:v for v in after},path)
    return [{'path':path or '配置','before':redacted(before),'after':redacted(after)}]


class ConfigHistory:
    def __init__(self,root,clock=time.time):
        self.root,self.clock=Path(root),clock
        self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'config-history.sqlite'
        key_path=self.root/'.config-history.key'
        if not key_path.exists() and self.path.exists():
            raise RuntimeError('Configuration history encryption key is missing; restore its matching key from backup')
        try:
            fd=os.open(key_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError: pass
        else:
            with os.fdopen(fd,'wb') as stream: stream.write(secrets.token_bytes(32))
        self.key=key_path.read_bytes()
        if len(self.key)!=32: raise RuntimeError('Invalid configuration history encryption key')
        with closing(self.connect()) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS config_history (
                id INTEGER PRIMARY KEY, created REAL, kind TEXT, entity TEXT,
                source TEXT, state TEXT, payload BLOB)''');db.commit()

    def connect(self): return sqlite3.connect(self.path,timeout=10)

    @staticmethod
    def encoded(value): return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()

    def fingerprint(self,value): return hmac.new(self.key,self.encoded(value),hashlib.sha256).hexdigest()

    def verify(self,expected,current):
        if not isinstance(expected,str) or not hmac.compare_digest(expected,self.fingerprint(current)):
            raise HTTPException(409,'配置已发生变化，请重新预览差异后再恢复')

    def seal(self,before,after):
        plain=self.encoded({'before':before,'after':after})
        if len(plain)>4*1024*1024: raise HTTPException(413,'单项配置过大，无法保存版本')
        nonce=secrets.token_bytes(12)
        return nonce+AESGCM(self.key).encrypt(nonce,plain,b'unified2api-config-history-v1')

    def get(self,rid):
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            row=db.execute('SELECT * FROM config_history WHERE id=?',(rid,)).fetchone()
        if row is None: raise HTTPException(404,'配置版本不存在或已超过保留时间')
        row=dict(row);blob=row.pop('payload')
        try: value=json.loads(AESGCM(self.key).decrypt(blob[:12],blob[12:],b'unified2api-config-history-v1'))
        except Exception: raise HTTPException(500,'配置历史解密失败，请检查备份中的加密密钥') from None
        return row,value

    def record(self,kind,entity,before,after,source='edit',state='completed'):
        if before==after and state=='completed': return None
        blob=self.seal(before,after)
        with closing(self.connect()) as db:
            cursor=db.execute('INSERT INTO config_history(created,kind,entity,source,state,payload) VALUES(?,?,?,?,?,?)',
                              (self.clock(),kind,entity,source,state,blob))
            db.execute('DELETE FROM config_history WHERE created<? OR id NOT IN (SELECT id FROM config_history ORDER BY id DESC LIMIT 500)',(self.clock()-90*86400,))
            db.commit();return cursor.lastrowid

    def reserve(self,kind,entity,before,source='edit'):
        return self.record(kind,entity,before,before,source,'pending')

    def complete(self,rid,after):
        _,value=self.get(rid)
        with closing(self.connect()) as db:
            if value['before']==after: db.execute('DELETE FROM config_history WHERE id=?',(rid,))
            else: db.execute("UPDATE config_history SET state='completed',payload=? WHERE id=?",(self.seal(value['before'],after),rid))
            db.commit()

    def target(self,rid,side='before'):
        if side not in ('before','after'): raise HTTPException(400,'请选择修改前或修改后的版本')
        row,value=self.get(rid)
        if side=='after' and row['state']!='completed': raise HTTPException(400,'此修改没有完整的修改后版本')
        return value[side]

    def detail(self,rid,current):
        row,value=self.get(rid)
        diff=changes(value['before'],value['after']) if row['state']=='completed' else []
        return {**row,'changes':diff[:100],'change_count':len(diff),'fingerprint':self.fingerprint(current),
                'restore_changes':changes(current,value['before'])[:100],
                'can_restore_before':True,'can_restore_after':row['state']=='completed'}

    def query(self,limit=30,offset=0,kind=''):
        try: limit=min(100,max(1,int(limit)));offset=max(0,int(offset))
        except (TypeError,ValueError): raise HTTPException(400,'分页参数无效')
        where=' WHERE kind=?' if kind else '';args=[kind] if kind else []
        with closing(self.connect()) as db:
            db.row_factory=sqlite3.Row
            total=db.execute('SELECT COUNT(*) FROM config_history'+where,args).fetchone()[0]
            rows=[dict(r) for r in db.execute('SELECT id,created,kind,entity,source,state FROM config_history'+where+' ORDER BY id DESC LIMIT ? OFFSET ?',args+[limit,offset])]
        return {'items':rows,'total':total,'retention_days':90,'max_versions':500}
