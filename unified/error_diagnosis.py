"""Fixed error taxonomy. Never returns or persists upstream free-form text."""
import json
import re

CODES = {'11102':'model_not_found','11128':'channel_verification','11133':'parameters','6004':'quota_exhausted'}
TEXT_CODES = {'model_not_found':'model_not_found','model_alias_disabled':'model_not_found',
              'context_length_exceeded':'context_length','invalid_request_error':'parameters',
              'insufficient_quota':'quota_exhausted','rate_limit_exceeded':'rate_limited',
              'first_token_timeout':'first_token_timeout','stream_idle_timeout':'stream_timeout'}
MESSAGES = {
    'model_not_found':('模型不存在或未接入','从模型目录复制实际模型 ID；显示名称与路由名称不等于平台模型 ID。'),
    'channel_verification':('上游渠道校验失败','检查账号登录状态与渠道适配版本；重新登录后用单账号合成测试定位。'),
    'parameters':('请求参数不兼容','核对模型支持的推理强度、工具格式和输出上限，使用最小请求逐项测试。'),
    'quota_exhausted':('上游额度不足','查询账号余额或等待额度恢复，切换到有额度的账号或渠道。'),
    'rate_limited':('请求受到限流','降低并发并等待冷却；查看尝试链确认限流发生的渠道。'),
    'context_length':('上下文超过模型限制','减少输入长度或选择更大上下文的模型。'),
    'authentication':('凭据失效或没有权限','刷新或重新登录该账号，检查模型权限。'),
    'first_token_timeout':('等待首个有效输出超时','检查上游推理耗时与首响应超时设置。'),
    'stream_timeout':('流式响应等待超时','检查上游停顿、代理空闲超时和连接状态；已输出内容不自动重放。'),
    'stream_incomplete':('响应未完整结束','检查客户端取消、代理断连和上游结束标记，不能仅凭 HTTP 200 判断调用完成。'),
    'output_limit':('达到输出上限','提高输出预算；推理模型的思考也可能占用输出预算。'),
    'upstream_unavailable':('上游服务暂时不可用','检查渠道健康、账号可用性及路由尝试链。'),
    'http_error':('请求失败，具体原因未知','使用请求 ID 与上游错误码排查；旧记录无法补回未保存的错误码。'),
}


def classify_error(value, status=None):
    parts, codes = [], []
    def walk(item, depth=0):
        if depth>5: return
        if isinstance(item,dict):
            code=item.get('code')
            if isinstance(code,(str,int)): codes.append(str(code))
            for key in ('error','message','detail','response','extError'):
                if key in item: walk(item[key],depth+1)
        elif isinstance(item,str):
            text=item[:16384];parts.append(text.lower())
            codes.extend(re.findall(r'["\\]*code["\\]*\s*[:=]\s*["\\]*(11102|11128|11133|6004)\b',text))
    walk(value)
    for code in codes:
        if code in CODES: return {'error_category':CODES[code],'upstream_code':code}
    for code in codes:
        if code in TEXT_CODES: return {'error_category':TEXT_CODES[code],'upstream_code':code}
    text=' '.join(parts)
    if 'context' in text and any(w in text for w in ('too long','maximum','exceed')): category='context_length'
    elif 'stream' in text and 'timeout' in text: category='stream_timeout'
    elif status==429: category='rate_limited'
    elif status in (401,403): category='authentication'
    elif status in (502,503,504,524): category='upstream_unavailable'
    elif status is not None and status>=400: category='http_error'
    else: return {}
    return {'error_category':category,'upstream_code':None}


def diagnosis(row):
    category=row.get('error_category')
    if not category and row.get('outcome') in {'interrupted','stream_error','missing_completion','missing_finish_reason'}:
        category='stream_incomplete'
    if not category and row.get('finish_reason')=='length': category='output_limit'
    if not category and row.get('status',0) and row['status']>=400: category=classify_error({},row['status']).get('error_category')
    if category not in MESSAGES: return None
    label,suggestion=MESSAGES[category]
    return {'category':category,'label':label,'suggestion':suggestion,'upstream_code':row.get('upstream_code')}


def error_body(exc):
    detail=exc.detail
    error={'message':detail,'type':'rate_limit_error' if exc.status_code==429 else 'invalid_request_error'}
    if isinstance(detail,dict):
        error.update({k:v for k,v in detail.items() if k in {'message','code','suggestions'}})
        error['message']=detail.get('message','请求失败')
    return {'error':error}
