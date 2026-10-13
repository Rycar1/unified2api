import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from unified.qoder_accounts_hub import QoderAccounts
from unified.qoder_hub import load_hub
load_hub()


class Benefits(unittest.IsolatedAsyncioTestCase):
    async def test_pro_claim_uses_actual_reward_and_refreshes_balance(self):
        manager=object.__new__(QoderAccounts)
        account=SimpleNamespace(access_token='test-access',refresh_token='test-refresh',personal_token='',
            credits={},clear_error=lambda:None)
        manager.locks={}
        manager.get=lambda _:account
        manager.save=lambda _:None
        manager.public=lambda _:{'status':'ready'}
        manager.transport=SimpleNamespace(refresh=AsyncMock(),request=AsyncMock(return_value={
            'userQuota':{'remaining':1800}}))
        with patch('qoder_tasks.run_pro_claim',return_value={'ok':True,'earned_credit':1800,'state':'claimed_now','msg':'Claimed'}):
            result=await manager.operate('one','pro_claim')
        self.assertEqual(result['earned_credit'],1800)
        self.assertEqual(result['state'],'claimed_now')
        self.assertEqual(result['remaining'],1800)

    async def test_bulk_import_preserves_all_rows_and_reports_failed_row(self):
        manager=object.__new__(QoderAccounts)
        imported=[]
        async def one(body):
            value=body['credential']
            if value['uid']=='bad': raise ValueError('Invalid synthetic credential')
            imported.append(value['uid'])
            return {'id':value['uid']}
        manager.import_single=one
        result=await manager.import_account({'credential':[{'uid':'one'},{'uid':'bad'},{'uid':'two'}]})
        self.assertEqual(imported,['one','two'])
        self.assertEqual(result['imported'],2)
        self.assertEqual(result['failed'],1)

    async def test_daily_checkin_still_only_claims_daily_campaigns(self):
        manager=object.__new__(QoderAccounts)
        account=SimpleNamespace(access_token='',refresh_token='',personal_token='',credits={},clear_error=lambda:None)
        manager.locks={}; manager.get=lambda _:account; manager.save=lambda _:None; manager.public=lambda _:{'status':'ready'}
        manager.transport=SimpleNamespace(refresh=AsyncMock(),request=AsyncMock(return_value={}))
        with patch('qoder_tasks.run_checkin',return_value={'ok':True,'earned_credit':0}) as check:
            result=await manager.operate('one','checkin')
        self.assertTrue(check.call_args.kwargs['only_daily'])
        self.assertEqual(result['earned_credit'],0)
