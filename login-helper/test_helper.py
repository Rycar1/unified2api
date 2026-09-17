import unittest
from urllib.parse import urlencode
from unittest.mock import patch
from playwright.sync_api import sync_playwright, APIRequestContext
import helper


class HelperTest(unittest.TestCase):
    def test_uri_restrictions(self):
        data={'base':'http://localhost:8080','id':'a'*32,'token':'b'*43}
        uri=lambda d:'unified2api-login://monkeycode?'+urlencode(d)
        self.assertTrue(helper.parse_launch(uri(data))[0].startswith('http://localhost:8080/'))
        for base in ['https://evil.test','http://localhost:8080@evil.test','http://127.0.0.1.evil.test:8080','http://localhost:8080/path','http://localhost:9999','file:///tmp']:
            with self.assertRaises(helper.LoginError):helper.parse_launch(uri(data|{'base':base}))

    def test_isolated_browser_cookie_handoff_and_cleanup(self):
        calls=[]
        def request(endpoint,token,action,method='GET',body=None):
            calls.append((action,body))
            return {'state':'waiting','expires_in':30}
        def prepare(context):
            self.assertEqual(context.cookies(),[])
            context.route('https://monkeycode-ai.com/**', lambda route:route.fulfill(status=200,
                content_type='text/html',headers={'Set-Cookie':'monkeycode_ai_session=fake-test-session; Path=/; Secure; HttpOnly'},body='<h1>Mock official login</h1>'))
        class UserResponse:
            status=200
            def json(self):return {'code':0,'data':{'user':{'id':'mock-user'},'teams':[]}}
        with sync_playwright() as p:
            browser=helper.launch_browser(p,headless=True)
            try:
                with patch.object(APIRequestContext,'get',return_value=UserResponse()) as get:
                    helper.run_window(browser,'http://localhost:8080/test','fake-token',request,prepare)
                    self.assertEqual(get.call_args.args[0],'https://monkeycode-ai.com/api/v1/users/status')
                self.assertEqual(browser.contexts,[])
            finally:browser.close()
        complete=[body for action,body in calls if action=='complete']
        self.assertEqual(complete,[{'cookie':'monkeycode_ai_session=fake-test-session'}])
        self.assertFalse(any(action=='cancel' for action,_ in calls))


if __name__=='__main__':unittest.main()
