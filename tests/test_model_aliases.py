import threading
import unittest
from types import SimpleNamespace
from fastapi import HTTPException
from unified.features import UnifiedConfig
from unified.model_aliases import ModelAliases


class Aliases(unittest.TestCase):
    def setUp(self):
        self.config=UnifiedConfig(SimpleNamespace(lock=threading.RLock(),data={},save=lambda:None))
        self.aliases=ModelAliases(self.config)
        self.catalog=[{'id':'workbuddy/glm-5.3-flash','capabilities':{'tools':True}}, {'id':'route/glm-53-flash'}]

    def test_explicit_alias_preserves_capabilities_and_resolves_one_hop(self):
        self.aliases.save({'id':'workbuddy/glm-53-flash','target':'workbuddy/glm-5.3-flash','name':'GLM 快速'},self.catalog)
        self.assertEqual(self.aliases.resolve('workbuddy/glm-53-flash',self.catalog),'workbuddy/glm-5.3-flash')
        row=self.aliases.models(self.catalog)[0]
        self.assertEqual(row['alias_target'],'workbuddy/glm-5.3-flash')
        self.assertTrue(row['capabilities']['tools'])

    def test_typo_suggests_canonical_model_without_silently_changing_it(self):
        with self.assertRaises(HTTPException) as err:
            self.aliases.resolve('workbuddy/glm-53-flash',self.catalog)
        self.assertEqual(err.exception.detail['code'],'model_not_found')
        self.assertIn('workbuddy/glm-5.3-flash',err.exception.detail['suggestions'])
        self.assertEqual(self.aliases.resolve('custom/undiscovered',self.catalog),'custom/undiscovered')

    def test_cannot_shadow_catalog_chain_alias_or_restore_stale_target(self):
        for body in ({'id':'route/glm-53-flash','target':'workbuddy/glm-5.3-flash'},
                     {'id':'a/x','target':'unknown/x'}):
            with self.assertRaises(HTTPException): self.aliases.save(body,self.catalog)
        self.aliases.save({'id':'a/x','target':'workbuddy/glm-5.3-flash'},self.catalog)
        with self.assertRaises(HTTPException): self.aliases.save({'id':'a/y','target':'a/x'},self.catalog)
        with self.assertRaises(HTTPException): self.aliases.resolve('a/x',[])
        self.aliases.save({'enabled':False},self.catalog,'a/x')
        with self.assertRaises(HTTPException): self.aliases.resolve('a/x',self.catalog)

