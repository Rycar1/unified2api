"""Credential pool operations; public views never contain stored credentials."""
from collections import defaultdict
import copy
import json
import math
import time
import uuid
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from admin.server import write_json
from .diagnostics import ResponseObservation, note_account
from .errors import safe_error_message


class CustomPool:
    def init_pool(self):
        self.active = defaultdict(int)
        self.cooldowns = {}
        self.pool_errors = {}
        self.cursors = defaultdict(int)
        for row in self.data.values():
            self.ensure_accounts(row)

    @staticmethod
    def ensure_accounts(row):
        if 'accounts' not in row:
            row['accounts'] = [{'id': 'primary', 'name': '默认账号', 'key': row.get('key', ''),
                                'enabled': True, 'max_concurrency': 2}]
        return row['accounts']

    def public_account(self, cid, account):
        pair = (cid, account['id'])
        return {k: copy.deepcopy(account[k]) for k in ('id', 'name', 'enabled', 'max_concurrency', 'balance') if k in account} | {
            'has_key': bool(account.get('key')), 'has_balance_key': bool(account.get('balance_key')),
            'balance_user_id': account.get('balance_user_id', ''), 'active': self.active[pair],
            'cooldown_until': self.cooldowns.get(pair, 0), 'last_error': self.pool_errors.get(pair, '')}

    def save_account(self, cid, body, aid=None):
        if not isinstance(body, dict) or set(body) - {'name', 'key', 'enabled', 'max_concurrency', 'balance_key', 'balance_user_id'}:
            raise HTTPException(400, '账号包含不支持的字段')
        with self.lock:
            row = self.get(cid)
            if row.get('kind') == 'opencode_runtime':
                raise HTTPException(400, '内置 OpenCode2API 由运行时管理凭据，无需添加账号 Key')
            accounts = self.ensure_accounts(row)
            old = next((a for a in accounts if a['id'] == aid), None) if aid else None
            if aid and old is None:
                raise HTTPException(404, '账号不存在')
            if not aid and len(accounts) >= 100:
                raise HTTPException(400, '每个服务最多添加 100 个账号')
            value = dict(old or {'id': uuid.uuid4().hex, 'name': '新账号', 'enabled': True, 'max_concurrency': 2})
            value.update({k: v for k, v in body.items() if k not in {'key', 'balance_key'} or v != ''})
            for field in ('key', 'balance_key', 'balance_user_id'):
                secret = value.get(field, '')
                if not isinstance(secret, str) or len(secret) > 8192 or any(c in secret for c in '\r\n\0'):
                    raise HTTPException(400, '凭据格式无效')
                value[field] = secret.strip()
            if not value['key'] or not isinstance(value['name'], str) or not 1 <= len(value['name'].strip()) <= 60:
                raise HTTPException(400, '请填写账号名称和 Key')
            value['name'] = value['name'].strip()
            if type(value['enabled']) is not bool or type(value['max_concurrency']) is not int or not 0 <= value['max_concurrency'] <= 100:
                raise HTTPException(400, '并发需要 0–100 的整数，启用状态必须为布尔值')
            credentials_changed = old is not None and any(value.get(k) != old.get(k) for k in ('key', 'balance_key', 'balance_user_id'))
            if credentials_changed:
                value.pop('balance', None)
            if old:
                accounts[accounts.index(old)] = value
            else:
                accounts.append(value)
            if accounts[0]['id'] == value['id']:
                row['key'] = value['key']
            updated = {**self.data, cid: row}
            write_json(self.path, updated)
            self.data = updated
            if credentials_changed or body.get('enabled') is True:
                self.cooldowns.pop((cid, value['id']), None)
                self.pool_errors.pop((cid, value['id']), None)
            return self.public_account(cid, value)

    def delete_account(self, cid, aid):
        with self.lock:
            row = self.get(cid)
            old = self.ensure_accounts(row)
            accounts = [a for a in old if a['id'] != aid]
            if len(old) == len(accounts):
                raise HTTPException(404, '账号不存在')
            if not accounts:
                raise HTTPException(400, '至少保留一个账号；不再使用时可删除整个服务')
            row.update(accounts=accounts, key=accounts[0]['key'])
            updated = {**self.data, cid: row}
            write_json(self.path, updated)
            self.data = updated
            return {'ok': True}

    def pool_inventory(self, cid, probe=False):
        row = self.get(cid)
        items = []
        for account in self.ensure_accounts(row):
            item = self.public_account(cid, account)
            exhausted = (account.get('balance') or {}).get('available') is False
            full = account.get('max_concurrency', 2) and item['active'] >= account.get('max_concurrency', 2)
            item['remaining'] = (account.get('balance') or {}).get('remaining')
            item['available'] = row['enabled'] and account['enabled'] and not exhausted and not full and (probe or item['cooldown_until'] <= time.time())
            item['status'] = 'paused' if not account['enabled'] else 'exhausted' if exhausted else 'busy' if full else 'cooling' if item['cooldown_until'] > time.time() else 'ready'
            items.append(item)
        return {'available': any(a['available'] for a in items), 'accounts': sum(a['available'] for a in items),
                'active': sum(a['active'] for a in items), 'candidates': items}

    async def pooled_relay(self, cid, path, body, scope, receive, send):
        row = self.get(cid)
        with self.lock:
            eligible = {r['id'] for r in self.pool_inventory(cid, probe=scope.get('state', {}).get('connection_probe', False))['candidates'] if r['available']}
            ready = [a for a in self.ensure_accounts(row) if a['id'] in eligible]
            offset = self.cursors[cid] % len(ready) if ready else 0
            self.cursors[cid] += 1
            ready = ready[offset:] + ready[:offset]
        if not ready:
            raise HTTPException(503, '自定义服务没有可用账号（已停用、余额不足、冷却或并发已满）')
        last = None
        for account in ready:
            pair = (cid, account['id'])
            with self.lock:
                if account.get('max_concurrency', 2) and self.active[pair] >= account.get('max_concurrency', 2):
                    continue
                self.active[pair] += 1
            emitted, pending = False, []
            observation = ResponseObservation(path)
            async def capture(event):
                nonlocal emitted
                observation.feed(event)
                if emitted:
                    return await send(event)
                pending.append(event)
                if observation.completed:
                    observation.finish()
                if observation.error and not observation.produced:
                    detail = b''.join(e.get('body', b'') for e in pending).decode('utf-8', errors='replace')
                    raise HTTPException(502, safe_error_message(detail, observation.status,
                        [a['key'] for a in self.ensure_accounts(row)]))
                if observation.produced or observation.terminal or observation.completed:
                    emitted = True
                    for buffered in pending:
                        await send(buffered)
                    pending.clear()
                elif sum(len(e.get('body', b'')) for e in pending) > (65536 if observation.stream else 8 * 1024 * 1024):
                    raise HTTPException(502, '上游尚未返回有效内容')
            scope.setdefault('state', {})['selected_account'] = {'id': account['id'], 'name': account['name']}
            note_account(cid, account['id'], account['name'])
            try:
                async def forward(emit):
                    return await self.relay_row({**row, 'key': account['key']}, path, body, scope, receive, emit)
                network = getattr(self, 'network', None)
                if network:
                    from .network import guarded_response
                    result = await guarded_response(forward, capture, path, network.get())
                else:
                    result = await forward(capture)
                for event in pending:
                    await send(event)
                # A missing protocol finish marker is diagnosed separately; it
                # does not establish an account authentication or quota failure.
                if observation.completed and not observation.error:
                    self.pool_errors.pop(pair, None)
                    self.cooldowns.pop(pair, None)
                else:
                    self.cooldowns[pair] = time.time() + 60
                    self.pool_errors[pair] = '上游响应未完整结束；已输出的内容没有重放'
                return result
            except HTTPException as exc:
                last = HTTPException(exc.status_code, safe_error_message(str(exc.detail), exc.status_code,
                    [a['key'] for a in self.ensure_accounts(row)] + [row.get('balance_key', '')]))
                if emitted or exc.status_code in {400, 404, 413, 422}:
                    raise last
                with self.lock:
                    self.cooldowns[pair] = time.time() + 60
                    self.pool_errors[pair] = str(last.detail)
            finally:
                with self.lock:
                    self.active[pair] = max(0, self.active[pair] - 1)
        raise last or HTTPException(429, '自定义账号并发已满')

    async def balance(self, cid, aid):
        row = self.get(cid)
        account = next((a for a in self.ensure_accounts(row) if a['id'] == aid), None)
        if account is None:
            raise HTTPException(404, '账号不存在')
        scheme = row.get('balance_scheme', 'none')
        base = row['base_url'].rstrip('/')
        origin = urlsplit(base)
        headers = {'Authorization': 'Bearer ' + account['key']}
        secrets = [a['key'] for a in row['accounts']] + [row.get('balance_key', ''), account.get('balance_key', '')]
        if scheme == 'deepseek':
            url = base.removesuffix('/v1') + '/user/balance'
        elif scheme == 'newapi':
            token = account.get('balance_key') or row.get('balance_key')
            if not token:
                raise HTTPException(400, 'NewAPI 余额查询需要控制台 Access Token')
            headers['Authorization'] = 'Bearer ' + token
            user_id = account.get('balance_user_id') or row.get('balance_user_id')
            if user_id:
                headers['New-Api-User'] = user_id
            url = f'{origin.scheme}://{origin.netloc}/api/user/self'
        elif scheme == 'sub2api':
            url = base + '/usage'
        else:
            raise HTTPException(400, '请先选择余额查询方案')
        try:
            from .custom import _bounded_body
            async with self.client() as client:
                async with client.stream('GET', url, headers=headers, timeout=20) as response:
                    raw = await _bounded_body(response, 256 * 1024)
                    if response.status_code != 200:
                        raise HTTPException(502, safe_error_message(raw, response.status_code, secrets))
                    payload = json.loads(raw)
            if not isinstance(payload, dict) or payload.get('success') is False or payload.get('error'):
                raise ValueError()
            def number(value):
                if isinstance(value, bool):
                    raise ValueError()
                result = float(value)
                if not math.isfinite(result):
                    raise ValueError()
                return result
            if scheme == 'deepseek':
                values = [{'remaining': number(v['total_balance']), 'unit': str(v['currency'])[:12]}
                          for v in payload['balance_infos']]
                if not values:
                    raise ValueError()
                value = {**values[0], 'balances': values, 'available': payload.get('is_available', any(v['remaining'] > 0 for v in values))}
            elif scheme == 'newapi':
                data = payload['data']
                remaining = number(data['quota'])
                value = {'remaining': remaining, 'used': number(data.get('used_quota', 0)), 'unit': 'quota', 'available': remaining > 0}
            else:
                data = payload.get('data', payload)
                quota = data.get('quota') or {}
                raw_remaining = data.get('remaining', data.get('balance', quota.get('remaining')))
                remaining = number(raw_remaining) if raw_remaining is not None else number(quota['limit']) - number(quota['used'])
                value = {'remaining': remaining, 'unit': str(data.get('unit', quota.get('unit', 'USD')))[:12], 'available': remaining > 0}
                if quota.get('used') is not None:
                    value['used'] = number(quota['used'])
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise HTTPException(502, '余额查询失败：连接异常或上游未返回有效的余额结构') from None
        value['updated'] = int(time.time())
        with self.lock:
            latest = self.get(cid)
            current = next((a for a in latest['accounts'] if a['id'] == aid), None)
            unchanged = current is not None and all(current.get(k) == account.get(k) for k in ('key', 'balance_key', 'balance_user_id'))
            unchanged = unchanged and all(latest.get(k) == row.get(k) for k in ('base_url', 'balance_scheme', 'balance_key', 'balance_user_id'))
            if unchanged:
                current['balance'] = value
                updated = {**self.data, cid: latest}
                write_json(self.path, updated)
                self.data = updated
            else:
                raise HTTPException(409, '账号配置已变更，本次查询结果未保存，请重新查询')
        return value
