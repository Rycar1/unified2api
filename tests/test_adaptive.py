import threading
import unittest
from types import SimpleNamespace
from fastapi import HTTPException
from unified.features import UnifiedConfig
from unified.adaptive import AdaptiveRoutes


class Adaptive(unittest.TestCase):
    def setUp(self):
        store = SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None)
        self.routes = AdaptiveRoutes(UnifiedConfig(store),clock=lambda:1000)
        self.models = {'a/model','b/model','c/model'}
        self.routes.save({'id':'smart','targets':list(sorted(self.models)),'strategy':'smart','retries':2},self.models)

    def test_smart_skips_zero_balance_cooling_and_saturation(self):
        inventory = {'a/model':{'available':False,'reason':'quota_exhausted'},
                     'b/model':{'available':True,'balance':None},'c/model':{'available':True}}
        self.routes.record('c/model',False,429,1,60)
        route,targets = self.routes.candidates('smart',inventory)
        self.assertEqual(targets,['b/model'])
        self.routes.active['b/model'] = route['max_concurrency']
        self.assertEqual(self.routes.candidates('smart',inventory)[1],[])

    def test_smart_prefers_health_and_load_without_comparing_currency_units(self):
        self.routes.record('a/model',True,200,5,60)
        self.routes.record('b/model',False,500,1,10)
        self.routes.health['b/model']['cooldown_until'] = 0
        inventory = {'a/model':{'available':True,'balance':2},'b/model':{'available':True,'balance':9999999}}
        self.assertEqual(self.routes.candidates('smart',inventory)[1][0],'a/model')
        self.routes.active['a/model'] = 7
        self.routes.health['b/model']['recent'] = [True]*20
        self.assertEqual(self.routes.candidates('smart',inventory)[1][0],'b/model')

    def test_reservation_is_atomic_and_released_even_after_error(self):
        route = self.routes.get('smart')
        route['max_concurrency'] = 1
        with self.routes.reserve('a/model',route):
            with self.assertRaises(HTTPException):
                with self.routes.reserve('a/model',route):
                    pass
        self.assertEqual(self.routes.active['a/model'],0)

    def test_legacy_strategy_order_is_preserved(self):
        self.routes.save({'id':'legacy','targets':['b/model','a/model'],'strategy':'priority'},self.models)
        self.assertEqual(self.routes.candidates('legacy',{'b/model':{'available':True}})[1],['b/model','a/model'])

    def test_all_strategies_skip_exhausted_accounts_and_restore_after_recovery(self):
        for strategy in ('priority','round_robin','latency','smart'):
            with self.subTest(strategy=strategy):
                self.routes.save({'id':'recover','targets':['a/model','b/model'],
                                  'strategy':strategy,'retries':0},self.models,
                                 'recover' if any(r['id']=='recover' for r in self.routes.rows()) else None)
                route, targets = self.routes.candidates('recover',{'a/model':{'available':False}})
                self.assertEqual(targets,['b/model'])
                self.assertEqual(route['targets'],['a/model','b/model'])
                self.assertEqual(self.routes.candidates('recover',{'a/model':{'available':False},
                    'b/model':{'available':False}})[1],[])
                self.assertIn('a/model',self.routes.candidates('recover',{'b/model':{'available':False}})[1])

    def test_protocol_filter_precedes_retry_budget_and_preview_matches(self):
        self.routes.save({'id':'protocol','targets':['a/model','b/model','c/model'],
                          'strategy':'priority','retries':0},self.models)
        supported={'a/model':False,'b/model':False,'c/model':True}
        self.assertEqual(self.routes.candidates('protocol',{},supported=supported)[1],['c/model'])
        self.assertEqual(self.routes.explain('protocol',{},supported)['selected'],'c/model')

    def test_model_absence_is_temporary_and_bad_request_does_not_cool_whole_model(self):
        self.routes.record('a/model',False,400,1,60,error_category='model_not_found')
        self.assertEqual(self.routes.health['a/model']['cooldown_until'],1060)
        self.assertNotIn('a/model',self.routes.candidates('smart')[1])
        self.routes.clock=lambda:1061
        self.assertIn('a/model',self.routes.candidates('smart')[1])
        self.routes.record('b/model',False,400,1,60,error_category='parameters')
        self.assertIn('b/model',self.routes.candidates('smart')[1])

    def test_all_cooling_does_not_force_retry_before_expiry(self):
        self.routes.save({'id':'cold','targets':['a/model'],'strategy':'priority'},self.models)
        self.routes.record('a/model',False,429,1,60)
        self.assertEqual(self.routes.candidates('cold')[1],[])

    def test_edit_preserves_configured_target_hidden_from_current_catalog(self):
        self.routes.save({'id':'smart','name':'Edited','strategy':'smart',
                          'targets':['a/model','b/model']},{'b/model'},'smart')
        self.assertEqual(self.routes.get('smart')['targets'],['a/model','b/model'])
        with self.assertRaises(HTTPException):
            self.routes.save({'id':'smart','targets':['a/model','unknown/model']},{'b/model'},'smart')
