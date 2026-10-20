"""Fixed account refusal codes, shared by the picker and private HTTP bridge."""
import time

REASONS={
    'account_missing':'账号已不存在',
    'account_paused':'账号已停用',
    'account_credentials_missing':'账号缺少登录凭据',
    'account_credential_refresh':'账号凭据刷新失败，请重新登录',
    'account_cooling':'账号或当前模型正在冷却',
    'account_reserve_balance':'余额低于账号保留线',
    'account_daily_tokens':'账号今日 Token 上限已达到',
    'account_daily_credits':'账号今日积分消耗上限已达到',
    'account_model_daily_limit':'该模型今日 Token 上限已达到',
    'account_realm':'账号站点与请求不匹配',
    'account_no_balance':'账号余额不足',
    'account_concurrency':'账号并发已满',
}


def public_gate(row,model=None):
    if row.get('reserveBlocked'):return 'account_reserve_balance'
    if row.get('dailyLimitBlocked'):return 'account_daily_tokens'
    limit=row.get('modelDailyTokenLimit') or 0
    used=(row.get('modelDailyTokens') or {}).get(model,0)
    if model and isinstance(limit,(int,float)) and limit>0 and isinstance(used,(int,float)) and used>=limit:
        return 'account_model_daily_limit'
    return None


def native_gate(account,model=None,refresh_failed=False):
    """Use the native guard methods without refreshing tokens in previews."""
    if account is None:return 'account_missing'
    if not account.enabled:return 'account_paused'
    if not account.access_token:return 'account_credentials_missing'
    if account.throttle_wait(model=model)>0:return 'account_cooling'
    if account.reserve_blocked():return 'account_reserve_balance'
    if account.daily_limit_blocked():return 'account_daily_tokens'
    if account.credit_limit_blocked(model):return 'account_daily_credits'
    if account.model_token_limit_blocked(model):return 'account_model_daily_limit'
    # An expired but refreshable token must still get its normal refresh attempt.
    return 'account_credential_refresh' if refresh_failed else None


def midnight():
    from datetime import datetime,timedelta
    return (datetime.fromtimestamp(time.time()).replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)).timestamp()
