"""Scoped maintenance operations reuse existing account controllers."""
import asyncio
from collections import defaultdict
from urllib.parse import quote

from fastapi import HTTPException
from .errors import safe_error_message

PROVIDERS = {'trae', 'codebuddy', 'monkeycode', 'qoder', 'qoder-cn', 'workbuddy_intl'}


class ScopedMaintenance:
    def __init__(self, store, pool, native, qoder, workbuddy, connections, task_center):
        self.store, self.pool, self.native = store, pool, native
        self.qoder, self.workbuddy, self.connections, self.task_center = qoder, workbuddy, connections, task_center

    async def accounts(self, providers):
        rows = []
        if 'codebuddy' in providers:
            rows += [{**r, 'provider': 'codebuddy'} for r in self.store.account_rows()]
        for provider, path in [('trae', '/admin/api/accounts'), ('monkeycode', '/monkey/admin/accounts')]:
            if provider not in providers:
                continue
            code, data = await self.native.request('GET', path)
            if code >= 400:
                raise HTTPException(502, provider + ' 账号列表查询失败')
            rows += [{**r, 'id': r['uid'], 'provider': provider,
                      'enabled': r.get('enabled', True) if provider == 'trae' else not r.get('disabled', False)} for r in data.get('accounts', [])]
        if providers & {'qoder', 'qoder-cn'}:
            rows += [{**r, 'provider': 'qoder-cn' if r.get('region') == 'cn' else 'qoder'} for r in await self.qoder.accounts()
                     if ('qoder-cn' if r.get('region') == 'cn' else 'qoder') in providers]
        if 'workbuddy_intl' in providers:
            rows += await self.workbuddy.public_accounts()
        for connection in self.connections.rows():
            if connection['id'] in providers:
                rows += [{**r, 'provider': connection['id'], 'enabled': r['enabled'] and connection['enabled']}
                         for r in connection['accounts'] if connection.get('balance_scheme', 'none') != 'none']
        return rows

    async def operate(self, provider, aid, action):
        if action == 'benefits':
            if provider not in {'codebuddy', 'qoder', 'qoder-cn'}:
                return {'ok': True, 'message': '此平台没有额外福利，已跳过'}
            results = []
            for task in (['benefits', 'streak', 'travel'] if provider == 'codebuddy' else ['benefits']):
                results.append(await self.task_center.run(provider, aid, task))
            return {'ok': all(r.get('ok') for r in results), 'message': '；'.join(r.get('message', '') for r in results)}
        if provider == 'codebuddy':
            return await asyncio.to_thread(self.pool.operate, aid, 'checkin' if action == 'checkin' else 'status')
        if provider in {'qoder', 'qoder-cn'}:
            return await self.qoder.operate(aid, action)
        if provider == 'workbuddy_intl':
            if action == 'refresh':
                await self.workbuddy.operate(aid, 'refresh')
                return await self.workbuddy.operate(aid, 'balance')
            return await self.workbuddy.operate(aid, action)
        if provider in {'trae', 'monkeycode'}:
            prefix = '/admin/api/accounts/' if provider == 'trae' else '/monkey/admin/accounts/'
            if action == 'checkin':
                code, data = await self.native.request('POST', prefix + quote(aid, safe='') + '/checkin', b'{}')
                if code >= 400:
                    raise HTTPException(code, provider + ' 签到失败')
                return data
            if action == 'refresh':
                code, _ = await self.native.request('POST', prefix + quote(aid, safe='') + '/refresh', b'{}')
                if code >= 400:
                    raise HTTPException(code, provider + ' 凭据刷新失败')
            return {'ok': True, **await self.task_center.quota(provider, aid)}
        return {'ok': True, **await self.connections.balance(provider, aid)}

    async def run(self, action, providers=None, account=None, exclude_accounts=None):
        allowed = PROVIDERS | {r['id'] for r in self.connections.rows()}
        selected = {account['provider']} if account else set(providers) if providers is not None else allowed
        if not selected <= allowed:
            raise HTTPException(400, '自动任务包含未知平台')
        excluded = {(r['provider'], r['account_id']) for r in exclude_accounts or []}
        groups = defaultdict(list)
        for provider in sorted(selected):
            try:
                rows = await self.accounts({provider})
            except HTTPException as exc:
                groups[provider].append({'ok': False, 'message': str(exc.detail)[:200]})
                continue
            if account:
                rows = [r for r in rows if r['id'] == account['account_id']]
                if not rows:
                    groups[provider].append({'ok': False, 'id': account['account_id'], 'message': '账号已删除或未配置余额查询方案'})
            for row in rows:
                aid = row['id']
                if (provider, aid) in excluded:
                    continue
                if not row.get('enabled', True):
                    if account:
                        groups[provider].append({'ok': True, 'id': aid, 'message': '账号已停用，本次跳过'})
                    continue
                try:
                    result = await self.operate(provider, aid, action)
                    groups[provider].append({'id': aid, 'provider': provider, 'ok': result.get('ok', True), **result})
                except HTTPException as exc:
                    groups[provider].append({'id': aid, 'ok': False, 'message': safe_error_message(str(exc.detail), exc.status_code)})
                except Exception:
                    groups[provider].append({'id': aid, 'ok': False, 'message': '操作失败，请检查账号状态或网络连接'})
        results = [row for group in groups.values() for row in group]
        return {'providers': [{'provider': p, 'results': r} for p, r in groups.items()], 'summary': {
            'total': len(results), 'succeeded': sum(bool(r.get('ok')) for r in results), 'failed': sum(not r.get('ok') for r in results)}}
