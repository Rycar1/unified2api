import threading
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from fastapi import HTTPException
from unified.features import AutomationManager, UnifiedConfig


class Schedules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = UnifiedConfig(SimpleNamespace(lock=threading.RLock(), data={}, save=lambda: None))
        self.now = [datetime(2026, 10, 16, 1, 30, tzinfo=timezone.utc).timestamp()]
        self.calls = []
        self.fail = False
        async def runner(action, **scope):
            self.calls.append((action, scope))
            return {'summary': {'total': 1, 'succeeded': int(not self.fail), 'failed': int(self.fail)},
                    'providers': [{'provider': 'codebuddy', 'results': [{'ok': not self.fail, 'message': 'synthetic failure' if self.fail else 'done'}]}]}
        self.runner = runner
        self.manager = AutomationManager(self.config, runner, clock=lambda: self.now[0])

    async def test_distinct_platform_schedules_use_beijing_time_and_persist_dedup(self):
        self.manager.update({'auto_checkin': True, 'schedules': [
            {'id': 'morning', 'label': 'CN', 'time': '09:00', 'providers': ['codebuddy'], 'enabled': True},
            {'id': 'evening', 'label': 'Intl', 'time': '20:00', 'providers': ['qoder'], 'enabled': True}]})
        await self.manager.tick()
        self.assertEqual(self.calls, [('checkin', {'providers': ['codebuddy']})])
        restarted = AutomationManager(self.config, self.runner, clock=lambda: self.now[0])
        await restarted.tick()
        self.assertEqual(len(self.calls), 1)
        self.now[0] += 12 * 3600
        await restarted.tick()
        self.assertEqual(self.calls[-1], ('checkin', {'providers': ['qoder']}))
        self.assertEqual(len(self.calls), 2)

    async def test_failed_job_has_retry_deadline_and_does_not_spin_each_tick(self):
        self.fail = True
        self.manager.update({'auto_checkin': True, 'schedules': [
            {'id': 'one', 'time': '08:00', 'providers': ['codebuddy'], 'enabled': True}]})
        await self.manager.tick()
        await self.manager.tick()
        self.assertEqual(len(self.calls), 1)
        state = self.manager.status()['schedules'][0]
        self.assertIn('synthetic failure', state['last_error'])
        self.assertGreater(state['next_run'], self.now[0])
        self.now[0] += 61
        self.fail = False
        await self.manager.tick()
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(self.manager.status()['schedules'][0]['last_ok'])

    async def test_per_account_interval_is_independent_and_survives_restart(self):
        self.manager.update({'account_refresh': [
            {'provider': 'codebuddy', 'account_id': 'a', 'minutes': 10, 'enabled': True},
            {'provider': 'workbuddy_intl', 'account_id': 'b', 'minutes': 30, 'enabled': True}]})
        await self.manager.tick()
        self.assertEqual(len(self.calls), 2)
        restarted = AutomationManager(self.config, self.runner, clock=lambda: self.now[0])
        await restarted.tick()
        self.assertEqual(len(self.calls), 2)
        self.now[0] += 601
        await restarted.tick()
        self.assertEqual(self.calls[-1], ('balance', {'account': {'provider': 'codebuddy', 'account_id': 'a'}}))
        self.assertEqual(len(self.calls), 3)

    async def test_invalid_schedule_update_is_atomic(self):
        original = self.manager.settings()
        for body in ({'schedules': [{'id': 'bad', 'time': '24:10', 'providers': ['codebuddy']}]},
                     {'account_refresh': [{'provider': 'codebuddy', 'account_id': 'a', 'minutes': 0}]},
                     {'schedules': [{'id': 'a', 'time': '08:00', 'providers': ['unknown']}]},
                     {'schedules': [{'id': 'a', 'time': '08:00', 'providers': ['codebuddy'], 'enabled': 'false'}]}):
            with self.assertRaises(HTTPException):
                self.manager.update(body)
            self.assertEqual(self.manager.settings(), original)
