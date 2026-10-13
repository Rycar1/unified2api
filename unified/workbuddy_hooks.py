"""Keep embedded account selection and parameter compatibility outside upstream code."""
import os
import inspect
import threading
from copy import deepcopy

_REQUEST=threading.local()


def request_parameter_metadata(payload):
    effort=payload.get('reasoning_effort',payload.get('reasoningEffort'))
    return {'messages':len(payload.get('messages') or []),'tools':len(payload.get('tools') or []),
        'reasoning_effort':effort if effort in {'none','off','low','medium','high','xhigh','max'} else 'other' if effort else None,
        'stream':payload.get('stream') is True}


def install(proxy):
    if getattr(proxy,'_unified_hooks_installed',False): return
    proxy._unified_hooks_installed=True
    # Upstream diagnostic strings may contain a search query or provider echo.
    # Unified persists structured counters only; client error responses stay intact.
    proxy.wb_webtools.set_logger(lambda *args,**kwargs:None)
    proxy.log=lambda *args,**kwargs:None
    original_error=proxy.record_error
    def record_error(model,status,message,*args,**kwargs):
        return original_error(model,status,'upstream request failed',*args,**kwargs)
    proxy.record_error=record_error
    original_build=proxy.build_upstream_body
    def build(payload):
        body=deepcopy(payload)
        effort=body.pop('reasoningEffort',None)
        if effort is not None and 'reasoning_effort' not in body: body['reasoning_effort']=effort
        if body.get('reasoning_effort')=='off':
            body.update(reasoning_effort='none',thinking={'type':'disabled'})
        explicit_effort='reasoning_effort' in body
        explicit_thinking='thinking' in body
        result=original_build(body)
        if not explicit_effort:
            result.pop('reasoning_effort',None)
            if not explicit_thinking: result.pop('thinking',None)
        return result
    proxy.build_upstream_body=build
    proxy.request_parameter_metadata=request_parameter_metadata
    handler=proxy.Handler
    original_post=handler.do_POST
    def post(self):
        _REQUEST.forced=self.headers.get('X-Unified-Account-Uid') if os.getenv('WORKBUDDY_EMBEDDED')=='true' else None
        _REQUEST.selected=None
        try: return original_post(self)
        finally: _REQUEST.forced=None; _REQUEST.selected=None
    handler.do_POST=post
    original_end=handler.end_headers
    def end(self):
        selected=getattr(_REQUEST,'selected',None)
        if selected and os.getenv('WORKBUDDY_EMBEDDED')=='true': self.send_header('X-Unified-Account',selected)
        return original_end(self)
    handler.end_headers=end
    pool_class=proxy.wb_accounts.AccountPool
    original_pick=pool_class.pick_for_session
    def pick(pool,*args,**kwargs):
        forced=getattr(_REQUEST,'forced',None)
        if forced:
            account=pool.get(forced)
            bound=inspect.signature(original_pick).bind(pool,*args,**kwargs)
            bound.apply_defaults()
            realm=bound.arguments.get('realm')
            excluded=bound.arguments.get('exclude') or set()
            if not account or account.uid in excluded or realm and account.realm!=realm or not account.ready(model=bound.arguments.get('model')):
                return None
        else: account=original_pick(pool,*args,**kwargs)
        if account: _REQUEST.selected=account.uid
        return account
    pool_class.pick_for_session=pick
