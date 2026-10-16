"""Persistent Beijing-time schedules and independent account balance timers."""
from datetime import datetime, timedelta, timezone
import re

from fastapi import HTTPException

CN = timezone(timedelta(hours=8))
CHECKIN_PROVIDERS = {'trae', 'codebuddy', 'monkeycode', 'qoder', 'qoder-cn', 'workbuddy_intl'}
IDENTIFIER = re.compile(r'[a-zA-Z0-9_.-]{1,100}')


def validate_plans(value, result):
    if 'schedules' in value:
        plans = value['schedules']
        if not isinstance(plans, list) or len(plans) > 10:
            raise HTTPException(400, '最多设置 10 条签到计划')
        normalized, ids = [], set()
        for plan in plans:
            if not isinstance(plan, dict):
                raise HTTPException(400, '签到计划格式无效')
            pid, label, at = plan.get('id'), plan.get('label', ''), plan.get('time')
            providers, enabled = plan.get('providers'), plan.get('enabled', True)
            if not isinstance(pid, str) or not IDENTIFIER.fullmatch(pid) or pid in ids:
                raise HTTPException(400, '计划 ID 无效或重复')
            if not isinstance(label, str) or len(label) > 60 or not isinstance(at, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', at):
                raise HTTPException(400, '计划名称最长 60 字，时间需要 HH:MM')
            if not isinstance(providers, list) or not providers or any(not isinstance(p, str) or p not in CHECKIN_PROVIDERS for p in providers) or type(enabled) is not bool:
                raise HTTPException(400, '请选择有效平台和计划开关')
            normalized.append({'id': pid, 'label': label.strip(), 'time': at, 'providers': list(dict.fromkeys(providers)), 'enabled': enabled})
            ids.add(pid)
        result['schedules'] = normalized
    if 'account_refresh' in value:
        rows = value['account_refresh']
        if not isinstance(rows, list) or len(rows) > 500:
            raise HTTPException(400, '最多设置 500 条账号刷新规则')
        normalized, ids = [], set()
        for row in rows:
            if not isinstance(row, dict):
                raise HTTPException(400, '账号刷新规则格式无效')
            provider, aid = row.get('provider'), row.get('account_id')
            minutes, enabled = row.get('minutes', 30), row.get('enabled', True)
            if not isinstance(provider, str) or not IDENTIFIER.fullmatch(provider) or not isinstance(aid, str) or not 1 <= len(aid) <= 256 or any(ord(c) < 32 for c in aid):
                raise HTTPException(400, '账号刷新规则缺少有效的平台或账号')
            if (provider, aid) in ids or type(minutes) is not int or not 5 <= minutes <= 1440 or type(enabled) is not bool:
                raise HTTPException(400, '刷新规则不能重复，间隔需要 5–1440 分钟')
            normalized.append({'provider': provider, 'account_id': aid, 'minutes': minutes, 'enabled': enabled})
            ids.add((provider, aid))
        result['account_refresh'] = normalized


class ScheduledAutomation:
    def jobs(self):
        settings = self.settings()
        plans = settings.get('schedules', [{'id': 'default', 'label': '默认签到',
                 'time': settings.get('checkin_time', '09:00'), 'providers': sorted(CHECKIN_PROVIDERS), 'enabled': True}])
        result = [{'key': 'checkin:' + p['id'], 'id': p['id'], 'label': p.get('label') or p['time'],
                   'time': p['time'], 'action': 'checkin', 'enabled': settings.get('auto_checkin', False) and p['enabled'],
                   'scope': {'providers': p['providers']}} for p in plans]
        if settings.get('auto_benefits'):
            result.append({'key': 'legacy:benefits', 'label': '领取福利', 'time': settings.get('checkin_time', '09:00'),
                           'action': 'benefits', 'enabled': True, 'scope': {}})
        overrides = [r for r in settings.get('account_refresh', []) if r['enabled']]
        if settings.get('auto_refresh'):
            result.append({'key': 'legacy:refresh', 'label': '默认账号刷新', 'action': 'refresh', 'enabled': True,
                           'minutes': settings.get('refresh_minutes', 30),
                           'scope': {'exclude_accounts': [{'provider': r['provider'], 'account_id': r['account_id']} for r in overrides]}})
        for row in settings.get('account_refresh', []):
            result.append({'key': 'balance:' + row['provider'] + ':' + row['account_id'],
                           'label': row['provider'] + ' · ' + row['account_id'], 'action': 'balance',
                           'enabled': row['enabled'], 'minutes': row['minutes'],
                           'scope': {'account': {'provider': row['provider'], 'account_id': row['account_id']}}})
        return result

    def next_run(self, job, state):
        now = self.clock()
        if not job['enabled']:
            return None
        if 'minutes' in job:
            due = state.get('last_attempt', 0) + job['minutes'] * 60
            return max(now, due, state.get('retry_at', 0), state.get('lease_until', 0))
        local = datetime.fromtimestamp(now, CN)
        hour, minute = map(int, job['time'].split(':'))
        due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        today = local.strftime('%Y-%m-%d')
        if state.get('last_day') == today and (state.get('last_ok') or state.get('failures', 0) >= 3):
            due += timedelta(days=1)
        return max(now, due.timestamp(), state.get('retry_at', 0), state.get('lease_until', 0))

    def task_state(self, job):
        state = self.config.get('automation_task_states', {}).get(job['key'], {})
        # Carry the legacy once-per-day marker across upgrades.
        if not state and job['key'] in {'checkin:default', 'legacy:benefits'}:
            record = next((r for r in self.config.get('automation_history', []) if r.get('action') == job['action']), None)
            if record:
                state = {'last_day': datetime.fromtimestamp(record['time'], CN).strftime('%Y-%m-%d'),
                         'last_attempt': record['time'], 'last_ok': record.get('ok', False), 'failures': 0}
        return state

    def status(self):
        items = []
        for job in self.jobs():
            state = self.task_state(job)
            items.append({**{k: v for k, v in job.items() if k != 'scope'}, **state, 'next_run': self.next_run(job, state)})
        return {'schedules': [r for r in items if r['action'] == 'checkin'],
                'account_refresh': [r for r in items if r['action'] == 'balance'], 'jobs': items}

    async def run_job(self, job, manual=False):
        now = self.clock()
        state = self.task_state(job)
        today = datetime.fromtimestamp(now, CN).strftime('%Y-%m-%d')
        if state.get('last_day') != today:
            state['failures'] = 0
        state.update(last_attempt=now, last_day=today, lease_until=now + 600)
        states = self.config.get('automation_task_states', {})
        states[job['key']] = state
        self.config.set('automation_task_states', states)
        report = await self.execute(job['action'], manual=manual, scope=job['scope'], task_id=job['key'], label=job['label'])
        record = report['record']
        errors = [str(row.get('message', '操作失败'))[:200] for group in report['result'].get('providers', [])
                  for row in group.get('results', []) if not row.get('ok')]
        state.update(last_ok=record['ok'], last_error=(record.get('error') or '；'.join(errors))[:500], lease_until=0)
        state['failures'] = 0 if record['ok'] else state.get('failures', 0) + 1
        state['retry_at'] = 0 if record['ok'] else self.clock() + min(60 * 2 ** min(state['failures'] - 1, 6), 3600)
        states = self.config.get('automation_task_states', {})
        states[job['key']] = state
        self.config.set('automation_task_states', states)
        return report

    async def tick(self):
        if self.running.locked():
            return
        for job in self.jobs():
            due = self.next_run(job, self.task_state(job))
            if due is not None and due <= self.clock():
                try:
                    await self.run_job(job)
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise

    async def run_plan(self, pid):
        job = next((j for j in self.jobs() if j.get('id') == pid), None)
        if job is None:
            raise HTTPException(404, '签到计划不存在')
        if self.running.locked():
            raise HTTPException(409, '已有自动任务正在运行')
        return await self.run_job(job, manual=True)
