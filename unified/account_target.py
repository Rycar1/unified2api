"""Admin-only, request-local account selection; never a public request flag."""
from contextvars import ContextVar
from dataclasses import dataclass
from fastapi import HTTPException

CURRENT_ACCOUNT = ContextVar('unified_account_target',default=None)


@dataclass
class AccountTarget:
    provider: str
    account_id: str
    confirmed: bool = False


def requested_account(provider):
    target=CURRENT_ACCOUNT.get()
    return target.account_id if target and target.provider==provider else None


def confirm_account(provider,aid):
    target=CURRENT_ACCOUNT.get()
    if target:
        if provider!=target.provider or str(aid)!=target.account_id:
            raise HTTPException(502,'测试账号不匹配，已停止测试，未将其他账号的响应作为成功结果')
        target.confirmed=True


async def validate_target(api,store,provider,aid,model):
    if not isinstance(provider,str):raise HTTPException(400,'请选择有效的平台账号')
    prefix={'workbuddy_intl':'workbuddy','qoder':'qoder','qoder-cn':'qoder-cn',
            'codebuddy':'codebuddy','trae':'trae','monkeycode':'monkeycode','antigravity':'antigravity'}.get(provider)
    if not prefix or not isinstance(aid,str) or not 1<=len(aid)<=200 or any(ord(c)<32 for c in aid):
        raise HTTPException(400,'请选择有效的平台账号')
    if model.partition('/')[0]!=prefix:
        raise HTTPException(400,'单账号测试必须选择该账号所属平台的模型，不支持智能路由')
    if api.aliases and any(r['id']==model for r in api.aliases.rows()):
        raise HTTPException(400,'单账号测试请选择真实模型，不支持模型别名')
    if prefix in {'trae','monkeycode'}:
        path='/admin/api/accounts' if prefix=='trae' else '/monkey/admin/accounts'
        code,data=await api.native.request('GET',path)
        if code!=200:raise HTTPException(503,'暂时无法读取平台账号状态')
        row=next((r for r in data.get('accounts',[]) if str(r.get('uid'))==aid),None)
        if row is None:raise HTTPException(404,'账号不存在')
        if row.get('enabled',True) is False or row.get('disabled'):
            raise HTTPException(409,'账号已停用，请先启用后测试')
    elif prefix=='codebuddy':
        row,_=api.buddy.state.pool.snapshot(aid)
        if not row.get('enabled',True):raise HTTPException(409,'账号已停用，请先启用后测试')
    elif prefix in {'qoder','qoder-cn'}:
        row=api.qoder.get(aid)
        if (row.realm=='cn')!=(prefix=='qoder-cn'):raise HTTPException(400,'账号站点与模型不匹配')
        if not row.enabled:raise HTTPException(409,'账号已停用，请先启用后测试')
    elif prefix=='antigravity':
        row=api.antigravity.get(aid)
        if not row['enabled']:raise HTTPException(409,'账号已停用，请先启用后测试')
    else:
        row=await api.workbuddy.account(aid)
        if not row.get('enabled'):raise HTTPException(409,'账号已停用，请先启用后测试')
    return AccountTarget(prefix,aid)
