"""Explicit one-hop model aliases and catalog-backed typo suggestions."""
from difflib import get_close_matches
import re
from fastapi import HTTPException

MODEL_ID = re.compile(r'^[a-z][a-z0-9_.-]{0,39}/[^\s\x00-\x1f]{1,256}$')
BUILTINS = {'trae','codebuddy','monkeycode','workbuddy','qoder','qoder-cn','antigravity','route'}


class ModelAliases:
    def __init__(self, config):
        self.config = config

    def rows(self):
        return self.config.get('model_aliases', [])

    def save(self, body, catalog, current=None):
        if not isinstance(body, dict) or set(body)-{'id','target','name','enabled'}:
            raise HTTPException(400, '别名包含不支持的字段')
        rows = self.rows()
        old = next((r for r in rows if r['id']==current), None) if current else None
        if current and old is None:
            raise HTTPException(404, '模型别名不存在')
        row = {**(old or {'enabled':True}), **body}
        ids = {r['id'] for r in catalog if not r.get('alias_target')}
        if any(not isinstance(row.get(k),str) or not MODEL_ID.fullmatch(row[k]) for k in ('id','target')):
            raise HTTPException(400, '请填写完整的 前缀/模型 ID')
        row.setdefault('name', row['id'])
        if not isinstance(row['name'],str) or not 1<=len(row['name'].strip())<=100 or type(row['enabled']) is not bool:
            raise HTTPException(400, '别名名称或启用状态无效')
        if current and row['id']!=current:
            raise HTTPException(400, '已有别名 ID 不可修改')
        if row['id'] in ids or row['target'] not in ids or row['id']==row['target']:
            raise HTTPException(400, '别名不能覆盖真实模型，目标必须是当前目录中的真实模型或路由')
        if not current and (len(rows)>=500 or any(r['id']==row['id'] for r in rows)):
            raise HTTPException(409, '别名已存在或已达到 500 条上限')
        row['name'] = row['name'].strip()
        self.config.set('model_aliases', [row if r['id']==current else r for r in rows] if current else rows+[row])
        return row

    def delete(self, mid):
        rows = self.rows()
        if not any(r['id']==mid for r in rows):
            raise HTTPException(404, '模型别名不存在')
        self.config.set('model_aliases', [r for r in rows if r['id']!=mid])
        return {'ok':True}

    def models(self, catalog):
        by_id = {r['id']:r for r in catalog}
        return [{**by_id[r['target']], 'id':r['id'], 'name':r['name'], 'owned_by':'alias',
                 'alias_target':r['target'], 'provider_name':'模型别名'} for r in self.rows()
                if r['enabled'] and r['target'] in by_id and r['id'] not in by_id]

    def resolve(self, mid, catalog, *, allow_unlisted=False):
        ids = {r['id'] for r in catalog if not r.get('alias_target')}
        row = next((r for r in self.rows() if r['id']==mid), None)
        if row:
            if mid in ids:
                raise HTTPException(409, {'code':'model_alias_conflict','message':'别名 ID 已与真实模型冲突，请删除旧别名后重新选择模型'})
            if not row['enabled']:
                raise HTTPException(400, {'code':'model_alias_disabled','message':'此模型别名已暂停'})
            if row['target'] not in ids:
                raise HTTPException(400, {'code':'model_not_found','message':'别名目标已不在模型目录中，请重新选择目标','suggestions':[]})
            return row['target']
        prefix = mid.partition('/')[0]
        candidates = sorted(i for i in ids if i.startswith(prefix+'/'))
        # An unavailable/empty provider catalog must not disable working APIs.
        # Custom services can deliberately use models not advertised by /models.
        if not allow_unlisted and prefix in BUILTINS and candidates and mid not in ids:
            suggestions = get_close_matches(mid, candidates, n=3, cutoff=.45)
            raise HTTPException(400, {'code':'model_not_found','message':'模型 ID 不在当前目录中，请检查实际请求模型；显示名称不会替代模型 ID',
                                     'suggestions':suggestions})
        return mid
