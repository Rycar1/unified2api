import asyncio
import base64
import hashlib
import json
import tempfile
import time
import unittest
from types import SimpleNamespace
from urllib.parse import urlparse, parse_qs
from unittest.mock import AsyncMock, patch
from pathlib import Path

import httpx
from fastapi import HTTPException
from admin.server import create_app as create_buddy
from unified.server import create_app
from unified.qoder import quota_balance, expiry
from unified.qoder_accounts_hub import QoderAccounts, model_aliases
from unified.qoder_hub import QoderHubTransport, load_hub

_accounts, _proxy, _sign, _catalog = load_hub()

def build_request(chat, user_type):
    return _proxy.build_qoder_body(chat, _accounts.Account({"uid": "synthetic", "realm": "intl", "userType": user_type}), chat["model"])

def encode(body):
    return _sign.qoder_encode(json.dumps(body).encode())

CUSTOM = _sign.QODER_CUSTOM_ALPHABET + _sign.QODER_PAD
STANDARD = _sign.QODER_STD_ALPHABET + "="
from unified.qoder_relay import request_to_chat


class Native:
    async def request(self, method, path, body=b''):
        return 200, {'accounts': [], 'data': []}


def envelope(chunk, status=200):
    return 'data: ' + json.dumps({'statusCodeValue': status, 'body': json.dumps(chunk)}) + '\n\n'


class QoderIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.mode, self.calls = 'success', []
        self.transport = QoderHubTransport(httpx.MockTransport(self.upstream))
        buddy = create_buddy(root=self.temp.name+'/management', auth_dir=self.temp.name+'/auth',
            initial_key='test-public', admin_key='long-admin-key-for-qoder-tests', secure_cookie=False)
        self.app = create_app(native=Native(), buddy=buddy, qoder_transport=self.transport)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test')
        login = await self.client.post('/admin/api/login',json={'key':'long-admin-key-for-qoder-tests'})
        self.csrf = {'X-CSRF-Token':login.json()['csrf']}
        self.headers = {'Authorization':'Bearer test-public'}
        self.account = await self.add('one')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.temp.cleanup()

    async def add(self, uid):
        response = await self.client.post('/admin/api/unified/qoder/accounts',headers=self.csrf,
            json={'credential':{'uid':uid,'securityOauthToken':'test-access-secret','refreshToken':'test-refresh-secret'},'name':uid})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def upstream(self, req):
        self.calls.append(req)
        path = req.url.path
        if path.endswith('/deviceToken/poll'):
            self.assertNotIn('Authorization',req.headers)
            if self.mode == 'oauth':
                return httpx.Response(200,json={'token':'test-access-secret','refresh_token':'test-refresh-secret','user_id':'oauth-user','expires_at':'2027-01-01T00:00:00Z'})
            return httpx.Response(404)
        if path.endswith('/refresh') or path.endswith('/jobToken/exchange'):
            return httpx.Response(200,json={'token':'test-access-secret','refresh_token':'jrt-test-refresh-secret','expires_in':86400000})
        if path.endswith('/userinfo'):
            return httpx.Response(200,json={'id':'one','name':'test','userType':'personal_standard'})
        if path.endswith('/user/status'):
            return httpx.Response(200,json={'id':'one','quota':0,'whitelistStatus':'PASS','isQuotaExceeded':True})
        if path.endswith('/quota/usage'):
            return httpx.Response(200,json={'userQuota':{'total':0,'used':0,'remaining':0},'isQuotaExceeded':True})
        if path.endswith('/model/list'):
            return httpx.Response(200,json={'chat':[{'key':'qfmodel','display_name':'Qwen3.8-Flash','enable':True,'is_vl':True}]})
        self.assertTrue(req.headers.get('authorization','').startswith('Bearer COSY.'))
        if self.mode == 'initial_error':
            data = envelope({'message':'test-access-secret token=test-refresh-secret'},403)
        else:
            data = envelope({'choices':[{'index':0,'delta':{'content':'OK','reasoning_content':'thinking'},'finish_reason':None}],
                'usage':{'prompt_tokens':4,'completion_tokens':2,'total_tokens':6}})
            if self.mode == 'mid_error':
                data += envelope({'code':'115','message':'upstream rejected'},502)
            elif self.mode == 'done_only':
                data += 'data: {"body":"[DONE]"}\n\n'
            elif self.mode != 'truncated':
                data += envelope({'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+'data: [DONE]\n\n'
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},text=data)

    async def invoke(self, path='chat/completions', stream=False, model='qoder/qfmodel'):
        return await self.client.post('/v1/'+path,headers=self.headers,json={'model':model,'stream':stream,
            'input':'synthetic test','messages':[{'role':'user','content':'synthetic test'}],'max_tokens':16})

    async def test_quota_zero_persisted_and_real_models_discovered(self):
        result = await self.client.post('/admin/api/unified/accounts/qoder/'+self.account['id']+'/balance',headers=self.csrf,json={})
        self.assertEqual(result.json()['remaining'],0)
        self.assertEqual(result.json()['status'],'exhausted')
        await self.client.post('/admin/api/unified/qoder/accounts/'+self.account['id']+'/refresh',headers=self.csrf,json={})
        overview = (await self.client.get('/admin/api/unified/overview')).json()
        self.assertEqual(overview['accounts'][0]['remaining'],0)
        self.assertEqual(overview['accounts'][0]['status'],'exhausted')
        self.assertNotIn('test-access-secret',json.dumps(overview))
        self.assertEqual(overview['connections'],[])
        models = (await self.client.get('/v1/models',headers=self.headers)).json()['data']
        model = next(m for m in models if m['id']=='qoder/qwen3.8-flash')
        self.assertNotIn('name',model)
        self.assertNotIn('qoder/qfmodel',[m['id'] for m in models])
        self.assertNotIn('qoder/auto',[m['id'] for m in models])
        restored = QoderAccounts(self.app.state.buddy.state.store,transport=self.transport)
        self.assertEqual((await restored.accounts())[0]['remaining'],0)

    async def test_three_protocols_streaming_and_nonstream(self):
        for path in ('chat/completions','responses','messages'):
            for stream in (False,True):
                response = await self.invoke(path,stream)
                self.assertEqual(response.status_code,200,response.text)
                self.assertIn('OK',response.text)
                if stream:
                    terminal = {'chat/completions':'[DONE]','responses':'response.completed','messages':'message_stop'}[path]
                    self.assertIn(terminal,response.text)
                else:
                    self.assertIsInstance(response.json(),dict)

    async def test_readable_aliases_dispatch_original_keys_in_all_protocols(self):
        for discovered in (False, True):
            if discovered:
                await self.client.post('/admin/api/unified/qoder/accounts/'+self.account['id']+'/refresh',headers=self.csrf,json={})
            for path in ('chat/completions','responses','messages'):
                for stream in (False,True):
                    with self.subTest(discovered=discovered,path=path,stream=stream):
                        response = await self.invoke(path,stream,model='qoder/qwen3.8-flash')
                        self.assertEqual(response.status_code,200,response.text)
                        call = next(req for req in reversed(self.calls) if 'agent_chat_generation' in req.url.path)
                        body = json.loads(_sign.qoder_decode(call.content.decode()))
                        self.assertEqual(body['model_config']['key'],'qfmodel')
            self.assertEqual((await self.invoke(model='qoder/qfmodel')).status_code,200)

    def test_duplicate_display_names_cannot_override_original_keys(self):
        rows = [{'key':'a','display_name':'Same'},{'key':'b','display_name':'Same'},
                {'key':'c','display_name':'a'},{'key':'qfmodel','display_name':'Qwen3.8-Flash'}]
        self.assertEqual(model_aliases(rows),{'a':'a','b':'b','c':'c','qfmodel':'qwen3.8-flash'})

    async def test_initial_error_preserves_status_and_redacts_secrets(self):
        self.mode = 'initial_error'
        response = await self.invoke(stream=True)
        self.assertEqual(response.status_code,403,response.text)
        self.assertNotIn('test-access-secret',response.text)
        self.assertNotIn('test-refresh-secret',response.text)
        self.assertNotIn('test-access-secret',json.dumps(await self.app.state.qoder_accounts.accounts()))

    async def test_midstream_failure_never_reports_success(self):
        self.mode = 'mid_error'
        for path in ('chat/completions','responses','messages'):
            [a.clear_error() for a in self.app.state.qoder_accounts.pool.accounts]
            response = await self.invoke(path,True)
            self.assertIn('upstream rejected',response.text)
            self.assertNotIn('response.completed',response.text)
            self.assertNotIn('message_stop',response.text)
            self.assertNotIn('"finish_reason":"stop"',response.text)

    async def test_truncated_nonstream_is_error(self):
        self.mode = 'truncated'
        response = await self.invoke()
        self.assertEqual(response.status_code,502,response.text)

    async def test_login_csrf_owner_pkce_and_completion(self):
        endpoint = '/admin/api/unified/qoder/login'
        self.assertEqual((await self.client.post(endpoint,json={})).status_code,403)
        started = await self.client.post(endpoint,headers=self.csrf,json={})
        flow_path = endpoint+'/'+started.json()['id']
        self.assertIn('challenge_method=S256',started.json()['login_url'])
        self.assertEqual((await self.client.get(flow_path)).json()['state'],'pending')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test') as other:
            await other.post('/admin/api/login',json={'key':'long-admin-key-for-qoder-tests'})
            self.assertEqual((await other.get(flow_path)).status_code,404)
        self.mode = 'oauth'
        self.assertEqual((await self.client.get(flow_path)).json()['state'],'success')
        self.assertEqual((await self.client.delete(flow_path,headers=self.csrf)).status_code,200)
        self.assertEqual((await self.client.get(flow_path)).status_code,404)

    async def test_crud_pool_rotation_and_empty_pool(self):
        second = await self.add('two')
        self.assertEqual(len([a for a in self.app.state.qoder_accounts.pool.accounts if a.enabled]),2)
        path = '/admin/api/unified/qoder/accounts/'+second['id']
        self.assertEqual((await self.client.patch(path,headers=self.csrf,json={'enabled':False,'name':'renamed'})).status_code,200)
        self.assertEqual(len([a for a in self.app.state.qoder_accounts.pool.accounts if a.enabled]),1)
        await self.client.delete(path,headers=self.csrf)
        await self.client.delete('/admin/api/unified/qoder/accounts/'+self.account['id'],headers=self.csrf)
        self.assertEqual((await self.invoke()).status_code,503)

    async def test_route_target_and_continuation(self):
        saved = await self.client.post('/admin/api/unified/routes',headers=self.csrf,json={'id':'qoder-test','name':'test',
            'targets':['qoder/qfmodel'],'strategy':'round_robin'})
        self.assertEqual(saved.status_code,200,saved.text)
        response = await self.invoke('responses',model='route/qoder-test')
        self.assertEqual(response.status_code,200,response.text)
        rid = response.json()['id']
        result = await self.client.post('/v1/responses',headers=self.headers,json={'model':'qoder/auto',
            'stream':False,'previous_response_id':rid,'input':'another synthetic request'})
        self.assertEqual(result.status_code,200,result.text)


    async def test_cn_and_global_same_uid_are_separate_pools(self):
        response = await self.client.post('/admin/api/unified/qoder/accounts',headers=self.csrf,
            json={'region':'cn','name':'China account','credential':{'uid':'one','accessToken':'cn-synthetic-token'}})
        self.assertEqual(response.status_code,200,response.text)
        cn = response.json()
        self.assertNotEqual(cn['id'],self.account['id'])
        self.assertEqual(len(await self.app.state.qoder_accounts.accounts()),2)
        await self.client.post('/admin/api/unified/qoder/accounts/'+cn['id']+'/refresh',headers=self.csrf,json={})
        models = (await self.client.get('/v1/models',headers=self.headers)).json()['data']
        self.assertTrue(any(m['id']=='qoder-cn/qwen3.8-flash' for m in models))
        self.calls.clear()
        result = await self.invoke(model='qoder-cn/qwen3.8-flash')
        self.assertEqual(result.status_code,200,result.text)
        chat_call = next(req for req in self.calls if 'agent_chat_generation' in req.url.path)
        self.assertEqual(chat_call.url.host,'gateway.qoder.com.cn')
        await self.client.patch('/admin/api/unified/qoder/accounts/'+cn['id'],headers=self.csrf,json={'enabled':False})
        self.assertEqual((await self.invoke(model='qoder-cn/qfmodel')).status_code,503)
        self.assertEqual((await self.invoke()).status_code,200)
        restored = QoderAccounts(self.app.state.buddy.state.store,transport=self.transport)
        self.assertEqual(len(await restored.accounts()),2)
        self.assertEqual(len(list(restored.root.joinpath('hub-accounts').glob('*.json'))),2)

    async def test_login_parameters_differ_between_regions(self):
        for region, host, cid in (('cn','qoder.com.cn','1c5e33e1-364d-4ce6-b02c-acaa81274a5c'),
                                 ('global','qoder.com','e883ade2-e6e3-4d6d-adf7-f92ceff5fdcb')):
            started = await self.client.post('/admin/api/unified/qoder/login',headers=self.csrf,json={'region':region})
            url = urlparse(started.json()['login_url'])
            args = parse_qs(url.query)
            self.assertEqual(url.hostname,host)
            self.assertEqual(args['client_id'],[cid])
            self.assertIn('machine_id',args)
            if region == 'cn':
                self.assertEqual(args['redirect_uri'],['qoder-work-cn://'])
                self.assertIn('-',args['nonce'][0])
            else:
                self.assertNotIn('redirect_uri',args)
            self.mode = 'oauth'
            complete = await self.client.get('/admin/api/unified/qoder/login/'+started.json()['id'])
            self.assertEqual(complete.json()['state'],'success',complete.text)
        rows = await self.app.state.qoder_accounts.accounts()
        self.assertEqual({row['region'] for row in rows},{'global','cn'})

    async def test_pat_exchange_and_expiring_tokens_refresh(self):
        result = await self.client.post('/admin/api/unified/qoder/accounts',headers=self.csrf,
            json={'region':'cn','credential':'pt-synthetic-personal-token'})
        self.assertEqual(result.status_code,200,result.text)
        account = self.app.state.qoder_accounts.get(result.json()['id'])
        self.assertTrue(account.refresh_token.startswith('jrt-'))
        self.assertTrue(any(req.url.path.endswith('/jobToken/exchange') for req in self.calls))
        self.calls.clear()
        account.expires_at = time.time()-1
        response = await self.invoke(model='qoder-cn/qfmodel')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(any(req.url.path.endswith('/jobToken/refresh') for req in self.calls))
        self.assertGreater(account.expires_at,time.time())
        self.assertNotIn('pt-synthetic-personal-token',json.dumps(await self.app.state.qoder_accounts.accounts()))

    async def test_legacy_migration_preserves_id_zero_and_disabled_state(self):
        root = Path(self.temp.name)/'legacy-store'
        qroot = root/'qoder'
        qroot.mkdir(parents=True)
        rows = [{'id':'existing-id','uid':'legacy-user','name':'Old name','enabled':False,'remaining':0,
            'expires_at':1798761600000,'credential':{'auth_user_info_raw':{'securityOauthToken':'synthetic-old-token',
            'refreshToken':'drt-synthetic-old-refresh','region':'global'}}}]
        (qroot/'accounts.json').write_text(json.dumps(rows),'utf-8')
        migrated = QoderAccounts(SimpleNamespace(root=root),transport=self.transport)
        current = await migrated.accounts()
        self.assertEqual(current[0]['id'],'existing-id')
        self.assertEqual(current[0]['name'],'Old name')
        self.assertEqual(current[0]['remaining'],0)
        self.assertFalse(current[0]['enabled'])
        self.assertTrue((qroot/'accounts.pre-hub.json').exists())
        self.assertFalse((qroot/'accounts.json').exists())
        reloaded = QoderAccounts(SimpleNamespace(root=root),transport=self.transport)
        self.assertEqual(len(await reloaded.accounts()),1)
        self.assertEqual(reloaded.get('existing-id').access_token,'synthetic-old-token')

    async def test_checkin_uses_hub_daily_activity_and_updates_balance(self):
        with patch('qoder_tasks.run_checkin',return_value={'ok':True,'campaign':'daily reward claimed','earned_credit':100}) as checkin:
            response = await self.client.post('/admin/api/unified/qoder/accounts/'+self.account['id']+'/checkin',
                headers=self.csrf,json={})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['remaining'],0)
            self.assertEqual(response.json()['earned_credit'],100)
            self.assertIn('daily reward claimed',response.json()['message'])
            self.assertTrue(checkin.call_args.kwargs['only_daily'])

    async def test_intl_daily_campaign_uses_native_identity_and_ignores_coupons(self):
        account = self.app.state.qoder_accounts.get(self.account['id'])
        account.credits = {'remain':0}
        identity = {'machineToken':'synthetic-machine-token','machineType':'synthetic-type',
                    'machineCode':'synthetic-code','source':'runtime-info'}
        credits = {'campaign_id':'daily-credit','campaign_key':'daily-credit','action_type':'CLAIM_BENEFIT',
                   'claim_status':'CLAIMABLE','benefit':{'kind':'CREDITS','amount':100}}
        coupon = {**credits,'campaign_id':'coupon','campaign_key':'coupon','benefit':{'kind':'REDEMPTION_CODE','amount':1}}
        with patch('qoder_accounts.native_machine_identity',return_value=identity), \
             patch.object(account,'campaigns',return_value={'ok':True,'campaigns':[credits,coupon]}), \
             patch.object(account,'claim_campaign',return_value={'ok':True,'amount':100}) as claim, \
             patch.object(account,'fetch_credits',return_value={'ok':True}), \
             patch.object(account,'fetch_plan'),patch('qoder_tasks.time.sleep'):
            headers = account.desktop_headers()
            self.assertEqual(headers['cosy-machinetoken'],'synthetic-machine-token')
            self.assertEqual(account.machine_headers_state,'native')
            response = await self.client.post('/admin/api/unified/qoder/accounts/'+self.account['id']+'/checkin',headers=self.csrf,json={})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(response.json()['earned_credit'],100)
        claim.assert_called_once_with('daily-credit')

    async def test_all_accounts_and_automation_use_intl_daily_checkin(self):
        for endpoint in ('/admin/api/unified/checkin','/admin/api/unified/automation/run/checkin'):
            with patch('qoder_tasks.run_checkin',return_value={'ok':True,'earned_credit':100,'campaign':'claimed'}) as claim, \
                 patch.object(self.app.state.workbuddy_login,'daily_active_all',new_callable=AsyncMock,return_value=[]):
                response = await self.client.post(endpoint,headers=self.csrf,json={})
            self.assertEqual(response.status_code,200,response.text)
            result = response.json().get('result',response.json())
            group = next(item for item in result['providers'] if item['provider']=='qoder')
            self.assertEqual(group['results'][0]['id'],self.account['id'])
            self.assertEqual(group['results'][0]['earned_credit'],100)
            self.assertTrue(claim.call_args.kwargs['only_daily'])

    async def test_upstream_done_gets_finish_reason_but_truncation_does_not(self):
        self.mode = 'done_only'
        response = await self.invoke(stream=True)
        self.assertEqual(response.status_code,200)
        self.assertIn('"finish_reason":"stop"',response.text)
        self.mode = 'truncated'
        response = await self.invoke(stream=True)
        self.assertNotIn('"finish_reason":"stop"',response.text)
        self.assertIn('未收到完成标记',response.text)


class WireFormat(unittest.TestCase):
    def test_responses_and_anthropic_images_preserve_tool_pairing(self):
        image_url='data:image/png;base64,c3ludGhldGlj'
        payload={'model':'qfmodel','input':[{'role':'user','content':[
            {'type':'input_text','text':'describe'},{'type':'input_image','image_url':image_url}]}]}
        body=build_request(request_to_chat(payload,'/v1/responses'),'personal_standard')
        self.assertEqual(body['image_urls'],[image_url])
        payload={'model':'qfmodel','max_tokens':16,'messages':[{'role':'user','content':[
            {'type':'tool_result','tool_use_id':'call-test','content':[{'type':'text','text':'tool text'},
                {'type':'image','source':{'type':'base64','media_type':'image/png','data':'c3ludGhldGlj'}}]},
            {'type':'text','text':'describe attachment'}]}]}
        converted=request_to_chat(payload,'/v1/messages')
        self.assertEqual(converted['messages'][0]['role'],'tool')
        self.assertEqual(converted['messages'][0]['tool_call_id'],'call-test')
        self.assertEqual(converted['messages'][0]['content'],'tool text')
        body=build_request(converted,'personal_standard')
        self.assertEqual(body['image_urls'],[image_url])
        self.assertIn('describe attachment',body['chat_context']['text']['text'])

    def test_quota_packages_and_unknown_are_distinct(self):
        self.assertIsNone(quota_balance({}))
        self.assertEqual(quota_balance({'userQuota':{'remaining':0}}),0)
        data={'userQuota':{'remaining':10},'addOnQuota':{'remaining':5},'dedicatedResourcePackages':[
            {'remaining':20,'available':True},{'remaining':100,'available':False},{'remaining':100,'status':'QUOTA_DETAIL_STATUS_EXPIRED'}]}
        self.assertEqual(quota_balance(data),35)

    def test_encode_decodes_to_actual_payload_and_no_default_effort(self):
        chat={'model':'qfmodel','messages':[{'role':'user','content':'test'}]}
        body=build_request(chat,'personal_standard')
        encoded=encode(body).translate(str.maketrans(CUSTOM,STANDARD))
        third=len(encoded)//3
        decoded=base64.b64decode(encoded[-third:]+encoded[third:-third]+encoded[:third])
        self.assertEqual(json.loads(decoded),body)
        self.assertEqual(body['model_config']['key'],'qfmodel')
        self.assertNotIn('reasoning_effort',body['parameters'])
        self.assertEqual(expiry('2027-01-01T00:00:00Z'),1798761600000)



if __name__=='__main__':
    unittest.main()
