import threading
import unittest
from types import SimpleNamespace
from unified.features import UnifiedConfig
from unified.credit_policy import CreditPolicy


class CreditPolicyTest(unittest.TestCase):
    def setUp(self):
        self.now=1792140000
        self.config=UnifiedConfig(SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None))
        self.policy=CreditPolicy(self.config,clock=lambda:self.now)

    def test_only_actual_unexpired_credit_packages_count(self):
        packages=[{'remain':20,'expire_time':self.now+86400},
                  {'remain':999,'expire_time':self.now+10,'no_expiry':True},
                  {'remain':500,'expire_time':self.now-100},
                  {'remain':0,'expire_time':self.now+10},
                  {'remain':1,'expire_time':self.now+10,'package_code':'enterprise'}]
        summary=self.policy.summarize(packages)
        self.assertEqual(summary['expiring_credits'],20)
        self.assertEqual(summary['credit_expires_at'],self.now+86400)
        self.assertEqual(self.policy.summarize([{'expires_at':self.now+10,'remain':100}])['credit_expires_at'],None)

    def test_tencent_fractional_packages_and_unknown_or_stale_data(self):
        summary=self.policy.summarize([{'CapacityRemainPrecise':'12.5','PackageEndTime':self.now+86400}])
        self.assertEqual(summary['expiring_credits'],12.5)
        self.assertLess(self.policy.priority(summary),self.policy.priority({}))
        self.assertEqual(self.policy.priority({**summary,'credit_fetched_at':self.now-86401}),self.policy.priority({}))
        self.policy.update({'enabled':False})
        self.assertEqual(self.policy.priority(summary),self.policy.priority({}))

    def test_account_selection_preserves_pause_cooldown_and_concurrency(self):
        from admin.pool import AccountPool
        rows=[{'id':n,'enabled':True,'status':'valid'} for n in ('regular','urgent')]
        data={'accounts':{n:{'name':n} for n in ('regular','urgent')},'active':'regular',
              'pool':{'routing':'round_robin'},'account_status':{'urgent':self.policy.summarize([{'remain':10,'expire_time':self.now+3600}])}}
        store=SimpleNamespace(lock=threading.RLock(),data=data,save=lambda:None,
                              account_rows=lambda:rows,manager_for=lambda aid,item:aid)
        pool=AccountPool(store,clock=lambda:self.now);pool.credit_policy=self.policy
        self.assertEqual(pool.select()[0],'urgent');pool.release('urgent')
        data['account_status']['urgent']['cooldown_until']=self.now+60
        self.assertEqual(pool.select()[0],'regular');pool.release('regular')
        data['account_status']['urgent']['cooldown_until']=0
        pool.active['urgent']=2
        self.assertEqual(pool.select(smart=True,max_concurrency=2)[0],'regular')

    def test_workbuddy_expiry_uses_fresh_package_deadline_and_preserves_unmanaged_accounts(self):
        from unified.workbuddy_hooks import install_credit_expiry
        class Account:
            def soonest_expiring_days(self): return 999
        install_credit_expiry(SimpleNamespace(wb_accounts=SimpleNamespace(Account=Account)))
        account=Account()
        self.assertEqual(account.soonest_expiring_days(),999)
        account._unified_credit_policy=self.policy
        account.credits={'fetched_at':self.now,'packages':[{'remain':10,'expire_time':self.now+2*86400}]}
        self.assertEqual(account.soonest_expiring_days(),2)
        account.credits['fetched_at']=self.now-90000
        self.assertIsNone(account.soonest_expiring_days())

    def test_changing_window_recalculates_cached_domestic_packages(self):
        import asyncio
        from unittest.mock import AsyncMock
        summary=self.policy.summarize([{'remain':10,'expire_time':self.now+2*86400},{'remain':20,'expire_time':self.now+5*86400}],store_packages=True)
        self.assertEqual(summary['expiring_credits'],30)
        store=SimpleNamespace(lock=threading.RLock(),data={'accounts':{'a':{'name':'Synthetic','enabled':True}},'account_status':{'a':summary}})
        self.policy.update({'window_days':3})
        result=asyncio.run(self.policy.expiring(store,SimpleNamespace(accounts=AsyncMock(return_value=[]))))
        self.assertEqual(result['items'][0]['expiring_credits'],10)

