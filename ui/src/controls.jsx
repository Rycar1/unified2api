import React, { useEffect, useState } from 'react'
import { Check, Download, RefreshCw, RotateCcw, Save } from 'lucide-react'
import './controls.css'

const count = value => Number(value || 0).toLocaleString('zh-CN')
const capabilityText = value => value === true ? '支持' : value === false ? '不支持' : '未知'
const protocolText = value => ({ native: '原生', converted: '格式转换', supported: '支持', unsupported: '不支持', unknown: '未知' }[value] || '未知')
const outcomeText = value => ({ success:'完成', http_error:'上游请求失败', stream_error:'流内错误', missing_completion:'缺少完成标记', missing_finish_reason:'缺少完成标记', interrupted:'连接中断', rejected:'请求被拒绝' }[value] || value || '未知')

export function CapabilityBadges({ capabilities = {} }) {
  const tags = []
  for (const [key, label] of [['chat','Chat'],['responses','Responses'],['messages','Messages']]) {
    const value = capabilities.protocols?.[key]
    if (['native','converted','supported'].includes(value)) tags.push([label, value === 'converted' ? `${label} · 转换` : value === 'native' ? `${label} · 原生` : label])
  }
  if (capabilities.vision === true) tags.push(['vision','图片'])
  if (capabilities.tools === true) tags.push(['tools','工具'])
  if (capabilities.compact === true) tags.push(['compact','Codex 压缩'])
  if (capabilities.context_window) tags.push(['context',`${count(capabilities.context_window)} 上下文`])
  return <div className="capability-tags">{tags.length ? tags.map(([key,label]) => <span key={key}>{label}</span>) : <small className="muted">能力待确认</small>}</div>
}

export function KeyLimitsDialog({ apiKey, models, csrf, request, Modal, onClose, onSaved }) {
  const [form, setForm] = useState({ daily_tokens:0, monthly_tokens:0, max_concurrency:0, requests_per_minute:0, allowed_models:[], enabled:true, ...apiKey.policy })
  const [usage, setUsage] = useState(apiKey.usage || {})
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => { let live = true; request(`unified/key-limits/${apiKey.id}`).then(value => { if (live) { setForm(value.policy); setUsage(value.usage) } }).catch(err => { if (live) setError(err.message) }); return () => { live = false } }, [apiKey.id])
  const update = (key,value) => setForm(current => ({ ...current, [key]:value }))
  const toggle = model => update('allowed_models',form.allowed_models.includes(model) ? form.allowed_models.filter(item => item !== model) : [...form.allowed_models,model])
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { await request(`unified/key-limits/${apiKey.id}`,{method:'PATCH',body:form,csrf}); await onSaved() }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title={`用量限制 · ${apiKey.name}`} subtitle="密钥权限" onClose={onClose}><form onSubmit={save}>
    <div className="control-usage"><span>今日 <strong>{count(usage.day_tokens)}</strong></span><span>本月 <strong>{count(usage.month_tokens)}</strong></span><span>并发 <strong>{count(usage.active)}</strong></span></div>
    <div className="field-grid">{[['daily_tokens','每日 Token 上限',1e12],['monthly_tokens','每月 Token 上限',1e12],['max_concurrency','同时执行的请求数',1000],['requests_per_minute','每分钟请求数',100000]].map(([field,label,max]) => <div key={field}><label htmlFor={`limit-${field}`}>{label}</label><input id={`limit-${field}`} type="number" min="0" max={max} step="1" required value={form[field]} onChange={event => update(field,Number(event.target.value))}/></div>)}</div>
    <p className="field-help">0 表示不限制。调用前预占预计 Token，用上游返回的实际用量结算；未提供用量但已输出时，保守扣除预占数。每日和每月按北京时间重置。</p>
    {usage.estimated_tokens > 0 && <p className="field-help">本月包含 {count(usage.estimated_tokens)} 个保守估算 Token。</p>}
    <label htmlFor="key-model-filter">允许调用的模型</label><input id="key-model-filter" type="search" placeholder="搜索模型" value={query} onChange={event => setQuery(event.target.value)}/>
    <div className="head-actions control-inline"><span className="muted">{form.allowed_models.length ? `已允许 ${form.allowed_models.length} 个模型` : '允许全部模型'}</span><button type="button" className="link-button" onClick={() => update('allowed_models',[])}>允许全部</button></div>
    <div className="model-picker control-model-picker">{models.filter(model => model.id.toLowerCase().includes(query.trim().toLowerCase())).map(model => <label key={model.id} className={form.allowed_models.includes(model.id) ? 'selected' : ''}><input type="checkbox" checked={form.allowed_models.includes(model.id)} onChange={() => toggle(model.id)}/><code>{model.id}</code></label>)}</div>
    <p className="field-help">允许一个路由别名，也就允许该路由配置中的目标。留空允许全部模型。</p>
    <label className="control-checkbox"><input type="checkbox" checked={form.enabled} onChange={event => update('enabled',event.target.checked)}/>启用此密钥</label>
    {error && <p className="form-error" role="alert">{error}</p>}<button className="button primary wide" disabled={busy}><Save size={15}/>{busy ? '正在保存…' : '保存限制'}</button>
  </form></Modal>
}

export function RequestDetailsDialog({ requestId, request, Modal, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { let live = true; request(`unified/diagnostics/${encodeURIComponent(requestId)}`).then(value => { if (live) setData(value) }).catch(err => { if (live) setError(err.message) }); return () => { live = false } }, [requestId])
  return <Modal title="请求诊断" subtitle={requestId} onClose={onClose}>
    {error && <p className="form-error" role="alert">{error}</p>}
    {!data && !error && <p className="muted">正在读取诊断信息…</p>}
    {data && <><dl className="diagnostic-summary"><div><dt>请求结果</dt><dd>{outcomeText(data.outcome)} · HTTP {data.status ?? '—'}</dd></div><div><dt>实际目标</dt><dd><code>{data.resolved_model || data.model}</code></dd></div><div><dt>首响应时间</dt><dd>{data.ttft_ms == null ? '未收到有效输出' : `${data.ttft_ms} ms`}</dd></div><div><dt>结束原因</dt><dd>{data.finish_reason || outcomeText(data.outcome)}</dd></div><div><dt>Token</dt><dd>{count(data.prompt_tokens)} 输入 · {count(data.completion_tokens)} 输出</dd></div></dl>
      <h3 className="control-subtitle">尝试过程</h3><ol className="attempt-list">{(data.attempts || []).map((attempt,index) => <li key={index} className={attempt.outcome === 'success' ? 'success' : 'error'}><div><span className="attempt-number">{index + 1}</span><code>{attempt.model}</code><span className="muted">{outcomeText(attempt.outcome)}</span></div><small>账号：{attempt.account_name || attempt.account_id || '上游未提供标识'}{attempt.account_name && attempt.account_id ? ` · ${attempt.account_id}` : ''}</small><small>HTTP {attempt.status ?? '—'} · 首响应 {attempt.ttft_ms == null ? '—' : `${attempt.ttft_ms} ms`} · 总耗时 {attempt.duration_ms ?? '—'} ms</small></li>)}</ol>
      {!data.attempts?.length && <p className="muted">此记录没有尝试信息；旧版本记录仅保留原统计字段。</p>}
      <p className="field-help">只保存诊断元数据，不保存提示词、回复正文、请求头或密钥。</p></>}
  </Modal>
}

export function ModelCapabilitiesDialog({ model, csrf, request, Modal, onClose, onSaved }) {
  const [caps, setCaps] = useState(model.capabilities || {})
  const [form, setForm] = useState({ vision:model.capabilities?.vision ?? null, tools:model.capabilities?.tools ?? null, context_window:model.capabilities?.context_window ?? null })
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [error, setError] = useState('')
  const path = `unified/model-capabilities/${encodeURIComponent(model.id)}`
  const builtin = ['trae','monkeycode','codebuddy','workbuddy','qoder','qoder-cn','route'].includes(model.id.split('/')[0])
  const derived = model.id.startsWith('route/')
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { const result = await request(path,{method:'PATCH',body:form,csrf}); setCaps(result.capabilities); await onSaved(); setFeedback('能力标记已保存') }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  async function probe() {
    if (!window.confirm('检测会发送少量合成请求，可能消耗上游额度。是否继续？')) return
    setBusy(true); setError(''); setFeedback('正在验证工具调用和接口格式…')
    try { const result = await request('unified/capability-probe',{method:'POST',body:{model:model.id},csrf}); setCaps(result.capabilities); setForm(current => ({ ...current,tools:result.capabilities.tools })); await onSaved(); setFeedback('检测完成。未能证实的能力仍显示未知。') }
    catch (err) { setError(err.message); setFeedback('') } finally { setBusy(false) }
  }
  async function reset() {
    setBusy(true); setError('')
    try { const result = await request(path,{method:'DELETE',csrf}); setCaps(result.capabilities); setForm({vision:result.capabilities.vision,tools:result.capabilities.tools,context_window:result.capabilities.context_window}); await onSaved(); setFeedback('已恢复目录和检测结果') }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  const tristate = (field,label) => <div><label htmlFor={`cap-${field}`}>{label}</label><select id={`cap-${field}`} value={form[field] == null ? 'unknown' : String(form[field])} onChange={event => setForm(current => ({ ...current,[field]:event.target.value === 'unknown' ? null : event.target.value === 'true' }))}><option value="unknown">未知</option><option value="true">支持</option><option value="false">不支持</option></select></div>
  return <Modal title="模型能力" subtitle={model.id} onClose={onClose}>
    <dl className="diagnostic-summary">{[['chat','Chat Completions'],['responses','Responses'],['messages','Messages']].map(([field,label]) => <div key={field}><dt>{label}</dt><dd>{protocolText(caps.protocols?.[field])}</dd></div>)}<div><dt>Codex 远程压缩</dt><dd>{capabilityText(caps.compact)}</dd></div></dl>
    {derived && <p className="field-help">能力从路由目标推导。调整目标模型的能力标记后，此处会自动更新。</p>}
    {!derived && <form onSubmit={save}><div className="field-grid">{tristate('vision','图片输入')}{tristate('tools','工具调用')}</div><label htmlFor="cap-context">上下文长度（Token）</label><input id="cap-context" type="number" min="1" max="100000000" value={form.context_window ?? ''} placeholder="未知时留空" onChange={event => setForm(current => ({ ...current,context_window:event.target.value ? Number(event.target.value) : null }))}/>
      {!builtin && <><label htmlFor="cap-responses">Responses 接口</label><select id="cap-responses" value={form.protocols?.responses || caps.protocols?.responses || 'unknown'} onChange={event => setForm(current => ({ ...current,protocols:{responses:event.target.value} }))}><option value="unknown">未知</option><option value="native">原生</option><option value="converted">格式转换</option><option value="unsupported">不支持</option></select></>}
      <p className="field-help">自动目录、检测与人工标记分别记录来源。图片与上下文长度不会仅凭模型名称猜测；普通文本回复不能证明工具或压缩能力。</p>
      <div className="head-actions"><button className="button primary" disabled={busy}><Save size={15}/>保存标记</button><button type="button" className="button secondary" onClick={probe} disabled={busy}><RefreshCw size={15} className={busy ? 'spin' : ''}/>检测能力</button><button type="button" className="link-button" onClick={reset} disabled={busy}>恢复自动</button></div>
    </form>}{error && <p className="form-error" role="alert">{error}</p>}{feedback && <p className="result-banner" role="status">{feedback}</p>}
  </Modal>
}

const releasePhase = value => ({ queued:'等待执行',prepare:'准备版本',download:'下载代码',build:'构建新版本',switch:'切换服务',health:'健康检查',complete:'已完成',failed:'操作失败',launch:'启动任务' }[value] || value)

export function ReleasesPage({ request, csrf }) {
  const [state,setState] = useState(null)
  const [operation,setOperation] = useState(null)
  const [busy,setBusy] = useState(false)
  const [error,setError] = useState('')
  const [ticket,setTicket] = useState(null)
  async function load() { try { const value = await request('unified/releases'); setState(value); setOperation(current => current || value.operation) } catch (err) { setError(err.message) } }
  useEffect(() => { void load() }, [])
  useEffect(() => {
    if (!ticket || !['queued','running'].includes(operation?.status)) return undefined
    let live = true
    const timer = window.setInterval(async () => {
      try {
        const response = await fetch(`/admin/api/unified/releases/operations/${ticket.id}`,{headers:{'X-Release-Ticket':ticket.ticket},credentials:'same-origin'})
        if (!response.ok) return
        const value = await response.json()
        if (live) { setOperation(value); if (!['queued','running'].includes(value.status)) { setBusy(false); void load() } }
      } catch { /* The gateway is temporarily unavailable while it is replaced. */ }
    },2000)
    return () => { live = false; window.clearInterval(timer) }
  }, [ticket,operation?.status])
  async function check() { setBusy(true); setError(''); try { setState(await request('unified/releases/check',{method:'POST',body:{},csrf})) } catch (err) { setError(err.message) } finally { setBusy(false) } }
  async function run(action) {
    if (!window.confirm(action === 'upgrade' ? '升级会短暂切换服务。账号、密钥和数据卷将保留；健康检查失败会恢复旧版本。是否继续？' : '是否恢复上一个版本？当前数据卷将保留。')) return
    setBusy(true); setError('')
    try { const value = await request(`unified/releases/${action}`,{method:'POST',body:action === 'upgrade' ? {revision:state.latest.revision} : {},csrf}); setOperation(value.operation); setTicket(value) }
    catch (err) { setError(err.message); setBusy(false) }
  }
  const version = value => value && value !== 'dev' ? value.slice(0,12) : '本地开发版本'
  const running = ['queued','running'].includes(operation?.status)
  return <section className="content-card release-card"><div className="section-head"><div><span className="section-label">版本管理</span><h2>升级与回滚</h2><p className="muted">先构建、再切换，健康检查失败自动恢复。完成后可能需要重新登录。</p></div><button className="button secondary" onClick={check} disabled={busy || running}><RefreshCw size={15} className={busy ? 'spin' : ''}/>检查更新</button></div>
    {error && <div className="result-banner error" role="alert">{error}</div>}
    <div className="release-versions"><article><span>当前版本</span><strong>{version(state?.current)}</strong></article><article><span>最新版本</span><strong>{state?.latest ? version(state.latest.revision) : '尚未检查'}</strong></article><article><span>可回滚版本</span><strong>{state?.previous ? version(state.previous.revision) : '暂无'}</strong></article></div>
    {state && !state.available && <p className="result-banner error">{state.reason}</p>}
    {state?.latest?.upgrade_supported === false && <p className="field-help">仓库当前版本尚未包含网页升级功能，请发布新版后再升级。</p>}
    <div className="head-actions"><button className="button primary" onClick={() => run('upgrade')} disabled={!state?.available || !state?.latest?.upgrade_supported || state.latest.revision === state.current || busy || running}><Download size={15}/>升级到此版本</button><button className="button secondary" onClick={() => run('rollback')} disabled={!state?.available || !state?.previous || busy || running}><RotateCcw size={15}/>回滚上一版本</button></div>
    {operation && <div className={`release-progress ${operation.status === 'failed' ? 'error' : ''}`} role="status"><div><strong>{releasePhase(operation.phase)}</strong><span>{operation.progress || 0}%</span></div><progress value={operation.progress || 0} max="100"/>{operation.status === 'succeeded' && <p><Check size={15}/>版本操作完成，请刷新控制台。</p>}{operation.status === 'failed' && <p>{operation.rolled_back ? '新版本未通过检查，原版本已恢复。' : '操作未完成，请检查 Docker 与仓库连接。'} {operation.error_code}</p>}</div>}
  </section>
}
