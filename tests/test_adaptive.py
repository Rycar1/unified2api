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
        self.assertEqual(self.routes.candidates('legacy',{'b/model':{'available':False}})[1],['b/model','a/model'])
