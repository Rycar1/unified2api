"""Aggregate counters and timings only; unknown cache and price stay unknown."""
from contextlib import closing
import importlib
from pathlib import Path
import sqlite3
import sys
import time


def price_usage(row):
    if not row.get('prompt_tokens') and not row.get('completion_tokens'): return {}
    try:
        directory=Path(__file__).resolve().parents[1]/'vendor/workbuddy2api-hub'
        if str(directory) not in sys.path: sys.path.insert(0,str(directory))
        pricing=importlib.import_module('wb_pricing')
        model=str(row.get('resolved_model') or row.get('model') or '').partition('/')[2].lower()
        value=pricing.cost_for_row({**row,'model':model,'at':row.get('created',int(time.time()))},details=False)
        return {'cny':value['cny'],'source':value.get('source')} if value.get('known') else {}
    except (ImportError,OSError,ValueError,TypeError,KeyError): return {}


def performance_summary(log,days=30):
    days=max(0,min(int(days),365))
    cutoff=int(time.time())-days*86400 if days else 0
    columns='''COUNT(*) requests, SUM(ok) successful,
        SUM(prompt_tokens) input_tokens, SUM(completion_tokens) output_tokens,
        SUM(cached_tokens) cached_tokens, COUNT(cached_tokens) known_cache_requests,
        SUM(CASE WHEN cached_tokens IS NOT NULL THEN prompt_tokens ELSE 0 END) cache_input_tokens,
        AVG(ttft_ms) average_ttft_ms, AVG(tokens_per_second) tokens_per_second,
        SUM(cost_cny) estimated_cost_cny, COUNT(cost_cny) priced_requests'''
    with log.lock,closing(log.connect()) as db:
        db.row_factory=sqlite3.Row
        summary=dict(db.execute('SELECT '+columns+' FROM requests WHERE created>=?',(cutoff,)).fetchone())
        models=[dict(row) for row in db.execute('SELECT COALESCE(resolved_model,model) model,'+columns+
            ' FROM requests WHERE created>=? GROUP BY COALESCE(resolved_model,model) ORDER BY requests DESC LIMIT 100',(cutoff,))]
        providers=[dict(row) for row in db.execute('SELECT provider, SUM(credits) credits,COUNT(credits) known_credit_requests'+
            ' FROM requests WHERE created>=? GROUP BY provider',(cutoff,))]
        series=[dict(row) for row in db.execute("SELECT date(created,'unixepoch','localtime') day,"+columns+
            " FROM requests WHERE created>=? GROUP BY day ORDER BY day DESC LIMIT 31",(cutoff,))][::-1]
    for row in [summary,*models,*series]:
        for field in ('requests','successful','input_tokens','output_tokens','cached_tokens','known_cache_requests','priced_requests'):
            row[field]=row.get(field) or 0
        row['cache_hit_percent']=round(row['cached_tokens']*100/row['cache_input_tokens'],2) if row.get('cache_input_tokens') else None
        row['success_rate']=round(row['successful']*100/row['requests'],2) if row['requests'] else None
    return {**summary,'days':days,'models':models,'providers':providers,'series':series,'cost_is_estimated':True}


def price_attempts(attempts):
    items=[item for item in attempts if item.get('kind')=='provider' and (item.get('prompt_tokens') or item.get('completion_tokens'))]
    if not items: return {}
    prices=[price_usage(item) for item in items]
    if any(price.get('cny') is None for price in prices): return {}
    return {'cny':sum(price['cny'] for price in prices),'source':'attempt-policies'}
