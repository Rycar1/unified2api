import React, { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import {
  Activity, Blocks, Bot, Check, ChevronDown, CircleGauge, Copy, Database, ExternalLink,
  Archive, Clock, Download, FileText, FlaskConical, KeyRound, LogOut, Menu, Network,
  Play, Plus, RefreshCw, Save, Search, Server, Settings2, Trash2, Upload, Users,
  WalletCards, X,
} from 'lucide-react'
import './styles.css'

const pageMeta = {
  overview: ['概览', '服务状态和常用入口'],
  accounts: ['账号', '统一管理所有平台账号'],
  connections: ['自定义服务', '接入 OpenAI 兼容接口'],
  models: ['模型', '查看当前可以调用的模型'],
  keys: ['API 密钥', '管理客户端访问凭据'],
  test: ['调用测试', '快速验证模型连接'],
  routes: ['智能路由', '设置模型别名、策略与自动故障转移'],
  logs: ['调用记录', '查看成功率、耗时和 Token 使用'],
  automation: ['自动任务', '定时签到、刷新余额和发送告警'],
  backup: ['备份恢复', '导出或恢复加密的完整服务数据'],
}

const navGroups = [
  { label: '工作台', items: [['overview', CircleGauge], ['accounts', Users], ['connections', Server], ['models', Bot]] },
  { label: '开发', items: [['keys', KeyRound], ['test', FlaskConical], ['routes', Network]] },
  { label: '运维', items: [['logs', FileText], ['automation', Clock], ['backup', Archive]] },
]

const providerName = id => ({ trae: 'TRAE', codebuddy: 'CodeBuddy', monkeycode: 'MonkeyCode', route: '智能路由' }[id] || id)
const statusName = value => ({ ready: '可用', available: '可用', paused: '已暂停', cooling: '冷却中', invalid: '凭据异常', exhausted: '额度耗尽', expired: '已过期', error: '异常' }[value] || value || '可用')

async function request(path, { method = 'GET', body, csrf = '' } = {}) {
  const headers = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf
  const response = await fetch(`/admin/api/${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const error = new Error(data.detail || data.error?.message || '请求失败，请稍后重试')
    error.status = response.status
    throw error
  }
  return data
}

function Login({ onLogin }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { await onLogin(key) } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <main className="login-shell">
    <section className="login-panel glass-panel">
      <div className="wordmark">Unified</div>
      <p className="kicker">本地模型网关</p>
      <h1>登录控制台</h1>
      <p className="muted">管理账号、模型和统一 API 接入。</p>
      <form onSubmit={submit}>
        <label htmlFor="admin-key">管理密钥</label>
        <input id="admin-key" type="password" value={key} onChange={e => setKey(e.target.value)} placeholder="输入 ADMIN_KEY" autoComplete="current-password" required />
        {error && <p className="form-error">{error}</p>}
        <button className="button primary wide" disabled={busy}>{busy ? '正在登录…' : '进入控制台'}</button>
      </form>
    </section>
  </main>
}

function StatusBadge({ status }) {
  const tone = ['ready', 'available'].includes(status) ? 'success' : ['paused', 'cooling'].includes(status) ? 'warning' : 'danger'
  return <span className={`status-badge ${tone}`}><i />{statusName(status)}</span>
}

function Empty({ children }) {
  return <div className="empty"><Database size={22} /><span>{children}</span></div>
}

function DataTable({ children, empty, columns }) {
  return <div className="table-shell"><table><thead><tr>{columns.map(c => <th key={c}>{c}</th>)}</tr></thead><tbody>{children}</tbody></table>{empty && <Empty>{empty}</Empty>}</div>
}

function Modal({ title, subtitle, onClose, children }) {
  return <div className="modal-backdrop" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <section className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
      <header className="modal-head"><div><span className="section-label">{subtitle}</span><h2>{title}</h2></div><button className="icon-action" onClick={onClose} aria-label="关闭"><X size={17}/></button></header>
      {children}
    </section>
  </div>
}

function AccountDialog({ csrf, onClose, onSaved }) {
  const [provider, setProvider] = useState('trae')
  const [mode, setMode] = useState('web')
  const [name, setName] = useState('')
  const [credential, setCredential] = useState('')
  const [flow, setFlow] = useState(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!flow) return undefined
    let stopped = false
    let timer
    async function poll() {
      try {
        let result
        if (flow.provider === 'trae') result = await request(`unified/trae/login/${flow.id}`)
        if (flow.provider === 'codebuddy') result = await request(`oauth/${flow.id}/poll`, { method: 'POST', body: {}, csrf })
        if (flow.provider === 'monkeycode') result = await request(`unified/monkeycode/login/${flow.id}`)
        if (stopped) return
        const state = result.state || result.status
        if (state === 'success') { setStatus('登录成功，账号已经保存'); await onSaved(); return }
        if (['failed', 'cancelled', 'expired'].includes(state)) { setFlow(null); setError(result.message || '登录已结束，请重新开始'); return }
        setStatus(state === 'validating' ? '正在校验并保存账号…' : '请在打开的官方页面完成登录…')
        timer = setTimeout(poll, 2500)
      } catch (err) {
        if (!stopped) { setFlow(null); setError(err.message) }
      }
    }
    timer = setTimeout(poll, 1200)
    return () => { stopped = true; clearTimeout(timer) }
  }, [flow, csrf, onSaved])

  async function cancelFlow() {
    if (!flow) return
    const path = flow.provider === 'trae' ? `unified/trae/login/${flow.id}` : flow.provider === 'codebuddy' ? `oauth/${flow.id}` : `unified/monkeycode/login/${flow.id}`
    try { await request(path, { method: 'DELETE', body: {}, csrf }) } catch { /* completed or expired */ }
    setFlow(null)
  }
  async function close() { await cancelFlow(); onClose() }
  async function startLogin() {
    setBusy(true); setError(''); setStatus('正在生成登录链接…')
    try {
      let result
      if (provider === 'trae') result = await request('unified/trae/login', { method: 'POST', body: {}, csrf })
      if (provider === 'codebuddy') result = await request('oauth/start', { method: 'POST', body: { name: name.trim() || undefined }, csrf })
      if (provider === 'monkeycode') result = await request('unified/monkeycode/login', { method: 'POST', body: { name: name.trim() }, csrf })
      const next = { provider, id: result.pending_id || result.id, url: result.login_url || result.url || result.launch_url }
      setFlow(next); setStatus('登录页面已打开，请完成官方登录')
      if (provider === 'monkeycode') {
        const link = document.createElement('a'); link.href = next.url; link.click()
      } else window.open(next.url, '_blank', 'noopener,noreferrer')
    } catch (err) { setError(err.message); setStatus('') } finally { setBusy(false) }
  }
  async function importAccount(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      let body = { name: name.trim() }
      if (provider === 'monkeycode') {
        const value = credential.trim()
        if (value.startsWith('{')) body.credential = JSON.parse(value)
        else body.cookie = value
      } else {
        body.credential = JSON.parse(credential)
      }
      const path = provider === 'trae' ? 'unified/trae/accounts' : provider === 'codebuddy' ? 'accounts' : 'unified/monkeycode/accounts'
      await request(path, { method: 'POST', body, csrf })
      await onSaved()
    } catch (err) { setError(err instanceof SyntaxError ? '请输入有效的 JSON 凭据' : err.message) } finally { setBusy(false) }
  }
  async function loadFile(event) {
    const file = event.target.files?.[0]
    if (!file) return
    if (file.size > 1024 * 1024) { setError('凭据文件不能超过 1 MB'); return }
    setCredential((await file.text()).replace(/^\uFEFF/, ''))
  }
  function changeProvider(next) { cancelFlow(); setProvider(next); setMode('web'); setStatus(''); setError(''); setCredential('') }

  return <Modal title="添加账号" subtitle="连接平台" onClose={close}>
    <div className="choice-grid">{[['trae','TRAE'],['codebuddy','CodeBuddy'],['monkeycode','MonkeyCode']].map(([id,label]) => <button key={id} className={provider === id ? 'active' : ''} onClick={() => changeProvider(id)}>{label}{provider === id && <Check size={14}/>}</button>)}</div>
    <label>账号备注（可选）</label><input value={name} onChange={e => setName(e.target.value)} maxLength="60" placeholder="例如：日常账号"/>
    <div className="mode-tabs"><button className={mode === 'web' ? 'active' : ''} onClick={() => setMode('web')}>网页登录</button><button className={mode === 'import' ? 'active' : ''} onClick={() => setMode('import')}>导入凭据</button></div>
    {mode === 'web' ? <div className="login-flow">
      <p>{provider === 'monkeycode' ? '通过本机登录助手打开独立窗口，完成登录后自动保存。' : '在平台官方页面完成登录，控制台会自动保存账号。'}</p>
      <button className="button primary wide" onClick={startLogin} disabled={busy || Boolean(flow)}><ExternalLink size={15}/>{busy ? '正在准备…' : flow ? '等待登录完成…' : '打开登录页面'}</button>
      {flow && <a className="button secondary wide" href={flow.url}>再次打开登录页面</a>}
      {provider === 'monkeycode' && <a className="helper-download" href="/admin/downloads/monkey-login-helper.zip" download>首次使用：下载 Windows 登录助手</a>}
      {status && <p className="flow-status">{status}</p>}
    </div> : <form onSubmit={importAccount}>
      <label>凭据文件</label><input type="file" accept=".json,.info,.txt" onChange={loadFile}/>
      <label>{provider === 'monkeycode' ? 'Cookie 或 JSON' : 'JSON 凭据'}</label><textarea value={credential} onChange={e => setCredential(e.target.value)} rows="7" required placeholder={provider === 'monkeycode' ? 'monkeycode_ai_session=…' : '{ "auth": { … }, "account": { … } }'}/>
      <button className="button primary wide" disabled={busy}>{busy ? '正在校验…' : '导入并添加'}</button>
    </form>}
    {error && <p className="form-error">{error}</p>}
  </Modal>
}

function ConnectionDialog({ csrf, onClose, onSaved }) {
  const [form, setForm] = useState({ name: '', id: '', base_url: '', key: '', models: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const body = () => ({ name: form.name.trim(), id: form.id.trim(), base_url: form.base_url.trim(), key: form.key.trim(), models: form.models.split(/\r?\n/).map(v => v.trim()).filter(Boolean) })
  async function discover() {
    setBusy(true); setError('')
    try { const result = await request('unified/connections/discover', { method: 'POST', body: body(), csrf }); update('models', result.models.join('\n')) }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const value = body()
      if (!value.models.length) throw new Error('请填写至少一个模型，或先读取模型列表')
      await request('unified/connections', { method: 'POST', body: value, csrf })
      await onSaved()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title="添加自定义服务" subtitle="OpenAI 兼容接口" onClose={onClose}><form onSubmit={save}>
    <div className="field-grid"><div><label>服务名称</label><input required maxLength="60" value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：我的模型服务"/></div><div><label>模型前缀</label><input required pattern="[a-z][a-z0-9_-]{0,39}" value={form.id} onChange={e => update('id', e.target.value)} placeholder="myapi"/></div></div>
    <label>Base URL</label><input type="url" required value={form.base_url} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com/v1"/>
    <label>API Key</label><input type="password" required value={form.key} onChange={e => update('key', e.target.value)} autoComplete="new-password" placeholder="服务商提供的 Key"/>
    <label>模型名称（每行一个）</label><textarea rows="5" value={form.models} onChange={e => update('models', e.target.value)} placeholder="model-name"/>
    <button className="button secondary wide" type="button" onClick={discover} disabled={busy}>读取模型列表</button>
    {error && <p className="form-error">{error}</p>}
    <button className="button primary wide" disabled={busy}>{busy ? '正在保存…' : '保存服务'}</button>
  </form></Modal>
}

function KeyDialog({ csrf, onClose, onSaved }) {
  const [name, setName] = useState('')
  const [created, setCreated] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { const result = await request('keys', { method: 'POST', body: { name }, csrf }); setCreated(result.key); await onSaved(false) }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title={created ? '保存新密钥' : '创建 API 密钥'} subtitle="客户端访问" onClose={onClose}>{created ? <div className="created-key"><p>完整密钥只显示一次，请立即复制并保存。</p><code>{created}</code><button className="button primary wide" onClick={() => navigator.clipboard.writeText(created)}><Copy size={15}/>复制密钥</button></div> : <form onSubmit={save}><label>密钥名称</label><input required maxLength="60" value={name} onChange={e => setName(e.target.value)} placeholder="例如：Cherry Studio"/>{error && <p className="form-error">{error}</p>}<button className="button primary wide" disabled={busy}>{busy ? '正在创建…' : '创建密钥'}</button></form>}</Modal>
}

function Overview({ data, onNavigate }) {
  const metrics = data?.metrics || {}
  const summary = [
    ['已连接账号', data?.accounts?.length || 0, Users],
    ['可用模型', data?.models?.length || 0, Blocks],
    ['自定义服务', data?.connections?.length || 0, Server],
    ['累计请求', metrics.completed || 0, Activity],
  ]
  return <div className="stack">
    <section className="summary-grid">{summary.map(([label, value, Icon]) => <article className="summary-card" key={label}><span className="summary-icon"><Icon size={16} /></span><div><strong>{value}</strong><span>{label}</span></div></article>)}</section>
    <section className="content-card endpoint-card">
      <div><span className="section-label">统一接口</span><h2>一个地址调用全部模型</h2><p className="muted">客户端只需配置 API 地址和模型前缀。</p></div>
      <code>{location.origin}/v1</code>
    </section>
    <section className="content-card">
      <div className="section-head"><div><span className="section-label">快速开始</span><h2>配置进度</h2></div></div>
      <div className="setup-list">
        <button onClick={() => onNavigate('accounts')}><span>01</span><div><strong>连接平台账号</strong><small>TRAE、CodeBuddy 或 MonkeyCode</small></div><ChevronDown size={16} /></button>
        <button onClick={() => onNavigate('keys')}><span>02</span><div><strong>创建 API 密钥</strong><small>供本地客户端访问统一网关</small></div><ChevronDown size={16} /></button>
        <button onClick={() => onNavigate('test')}><span>03</span><div><strong>发送测试请求</strong><small>确认模型路由和回复正常</small></div><ChevronDown size={16} /></button>
      </div>
    </section>
  </div>
}

function AccountsLegacy({ data, csrf, onRefresh, onAdd }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [checkingIn, setCheckingIn] = useState(false)
  const [checkinMessage, setCheckinMessage] = useState('')
  const rows = useMemo(() => (data?.accounts || []).filter(a => (provider === 'all' || a.provider === provider) && `${a.name || ''} ${a.nickname || ''} ${a.uid || ''}`.toLowerCase().includes(query.toLowerCase())), [data, provider, query])
  async function checkinAll() {
    setCheckingIn(true)
    setCheckinMessage('')
    try {
      const result = await request('unified/checkin', { method: 'POST', body: {}, csrf })
      const { total, succeeded, failed } = result.summary
      setCheckinMessage(total ? `签到完成：成功或已签到 ${succeeded} 个，失败 ${failed} 个` : '没有需要签到的启用账号')
      await onRefresh()
    } catch (error) {
      setCheckinMessage(error.message)
    } finally {
      setCheckingIn(false)
    }
  }
  return <section className="content-card">
    <div className="section-head"><div><span className="section-label">账号池</span><h2>{data?.accounts?.length || 0} 个账号</h2></div><div className="head-actions"><button className="button secondary" onClick={checkinAll} disabled={checkingIn}><Activity size={15}/>{checkingIn ? '正在签到…' : '一键签到'}</button><button className="button primary" onClick={onAdd}><Plus size={15}/>添加账号</button></div></div>
    {checkinMessage && <div className="result-banner" role="status">{checkinMessage}</div>}
    <div className="toolbar"><div className="filter-tabs">{[['all','全部'],['trae','TRAE'],['codebuddy','CodeBuddy'],['monkeycode','MonkeyCode']].map(([id,label]) => <button key={id} className={provider === id ? 'active' : ''} onClick={() => setProvider(id)}>{label}</button>)}</div><label className="search-box"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索账号" /></label></div>
    <DataTable columns={['账号','平台','状态','额度','有效期','']} empty={!rows.length && '没有匹配的账号'}>{rows.map(a => { const status = a.enabled === false ? 'paused' : a.pool_state || a.status || 'ready'; return <tr key={`${a.provider}-${a.id}`}><td><strong>{a.name || a.nickname || a.uid}</strong><small>{a.uid || a.id}</small></td><td><span className="provider-badge">{providerName(a.provider)}</span></td><td><StatusBadge status={status}/></td><td className="tabular">{typeof a.remaining === 'number' ? a.remaining.toLocaleString() : '—'}</td><td><span className="muted">{a.expires_at ? new Date(a.expires_at).toLocaleDateString('zh-CN') : '未提供'}</span></td><td className="row-menu"><button aria-label="账号操作">•••</button></td></tr> })}</DataTable>
  </section>
}

function accountEndpoint(account, suffix = '') {
  const id = encodeURIComponent(account.id)
  if (account.provider === 'codebuddy') return `accounts/${id}${suffix}`
  return `unified/${account.provider}/accounts/${id}${suffix}`
}

function AccountRowActions({ account, csrf, onRefresh, onFeedback }) {
  const [more, setMore] = useState(false)
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 })
  const [busy, setBusy] = useState('')
  const moreButton = useRef(null)
  const menu = useRef(null)

  useEffect(() => {
    if (!more) return undefined
    function close(event) {
      if (!moreButton.current?.contains(event.target) && !menu.current?.contains(event.target)) setMore(false)
    }
    function closeForViewportChange() { setMore(false) }
    window.addEventListener('pointerdown', close)
    window.addEventListener('resize', closeForViewportChange)
    window.addEventListener('scroll', closeForViewportChange, true)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('resize', closeForViewportChange)
      window.removeEventListener('scroll', closeForViewportChange, true)
    }
  }, [more])

  async function run(action) {
    setBusy(action)
    try {
      const path = action === 'status'
        ? `unified/accounts/${account.provider}/${encodeURIComponent(account.id)}/balance`
        : accountEndpoint(account, account.provider === 'codebuddy' ? `/actions/${action}` : `/${action}`)
      const result = await request(path, { method: 'POST', body: {}, csrf })
      if (result.ok === false) throw new Error(result.message || '账号操作失败')
      const balance = typeof result.remaining === 'number' ? `，当前余额 ${result.remaining.toLocaleString()}` : ''
      const fallback = action === 'checkin' ? '签到完成' : action === 'status' ? '余额已更新' : '刷新完成'
      onFeedback(account, (result.message || fallback) + balance, false)
      await onRefresh()
    } catch (error) {
      onFeedback(account, error.message, true)
    } finally {
      setBusy('')
    }
  }

  async function toggleEnabled() {
    setBusy('toggle')
    try {
      await request(accountEndpoint(account), { method: 'PATCH', body: { enabled: account.enabled === false }, csrf })
      onFeedback(account, account.enabled === false ? '账号已启用' : '账号已停用', false)
      setMore(false)
      await onRefresh()
    } catch (error) {
      onFeedback(account, error.message, true)
    } finally {
      setBusy('')
    }
  }

  async function remove() {
    const label = account.name || account.nickname || account.uid
    if (!window.confirm(`确定删除账号“${label}”吗？`)) return
    setBusy('delete')
    try {
      await request(accountEndpoint(account), { method: 'DELETE', csrf })
      onFeedback(account, '账号已删除', false)
      await onRefresh()
    } catch (error) {
      onFeedback(account, error.message, true)
    } finally {
      setBusy('')
    }
  }

  function toggleMore() {
    if (!more && moreButton.current) {
      const rect = moreButton.current.getBoundingClientRect()
      setMenuPosition({ top: rect.bottom + 7, left: Math.max(10, rect.right - 126) })
    }
    setMore(value => !value)
  }

  const disabled = Boolean(busy) || account.enabled === false
  return <div className="account-actions">
    <button className="mini-action" onClick={() => run('checkin')} disabled={disabled} title="签到并刷新额度"><Activity size={13}/><span>{busy === 'checkin' ? '签到中' : '签到'}</span></button>
    <button className="mini-action" onClick={() => run('refresh')} disabled={disabled} title="刷新凭据和额度"><RefreshCw size={13} className={busy === 'refresh' ? 'spin' : ''}/><span>{busy === 'refresh' ? '刷新中' : '刷新'}</span></button>
    <button className="mini-action" onClick={() => run('status')} disabled={disabled} title="查询最新余额"><WalletCards size={13}/><span>{busy === 'status' ? '查询中' : '余额'}</span></button>
    <button ref={moreButton} className={`dots-action ${more ? 'active' : ''}`} onClick={toggleMore} aria-label="更多账号操作" aria-expanded={more}>•••</button>
    {more && createPortal(<div ref={menu} className="account-popover" style={menuPosition} role="menu">
      <button role="menuitem" onClick={toggleEnabled} disabled={Boolean(busy)}>{account.enabled === false ? '启用账号' : '停用账号'}</button>
      <button role="menuitem" className="danger-text" onClick={remove} disabled={Boolean(busy)}>删除账号</button>
    </div>, document.body)}
  </div>
}

function Accounts({ data, csrf, onRefresh, onAdd }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [checkingIn, setCheckingIn] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const rows = useMemo(() => (data?.accounts || []).filter(account =>
    (provider === 'all' || account.provider === provider) &&
    `${account.name || ''} ${account.nickname || ''} ${account.uid || ''}`.toLowerCase().includes(query.toLowerCase())
  ), [data, provider, query])

  function showFeedback(account, message, error) {
    setFeedback({ key: `${account.provider}-${account.id}`, message, error })
  }

  async function checkinAll() {
    setCheckingIn(true)
    setFeedback(null)
    try {
      const result = await request('unified/checkin', { method: 'POST', body: {}, csrf })
      const { total, succeeded, failed } = result.summary
      setFeedback({ key: '', error: failed > 0, message: total ? `签到完成：成功或已签到 ${succeeded} 个，失败 ${failed} 个` : '没有需要签到的启用账号' })
      await onRefresh()
    } catch (error) {
      setFeedback({ key: '', error: true, message: error.message })
    } finally {
      setCheckingIn(false)
    }
  }

  return <section className="content-card">
    <div className="section-head"><div><span className="section-label">账号池</span><h2>{data?.accounts?.length || 0} 个账号</h2></div><div className="head-actions"><button className="button secondary" onClick={checkinAll} disabled={checkingIn}><Activity size={15}/>{checkingIn ? '正在签到…' : '全部签到'}</button><button className="button primary" onClick={onAdd}><Plus size={15}/>添加账号</button></div></div>
    {feedback && !feedback.key && <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">{feedback.message}</div>}
    <div className="toolbar"><div className="filter-tabs">{[['all','全部'],['trae','TRAE'],['codebuddy','CodeBuddy'],['monkeycode','MonkeyCode']].map(([id,label]) => <button key={id} className={provider === id ? 'active' : ''} onClick={() => setProvider(id)}>{label}</button>)}</div><label className="search-box"><Search size={15}/><input value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索账号" /></label></div>
    <DataTable columns={['账号','平台','状态','额度','有效期','操作']} empty={!rows.length && '没有匹配的账号'}>{rows.map(account => {
      const key = `${account.provider}-${account.id}`
      const status = account.enabled === false ? 'paused' : account.pool_state || account.status || 'ready'
      const remaining = account.remaining
      return <React.Fragment key={key}>
        <tr><td><strong>{account.name || account.nickname || account.uid}</strong><small>{account.uid || account.id}</small></td><td><span className="provider-badge">{providerName(account.provider)}</span></td><td><StatusBadge status={status}/></td><td className="tabular">{typeof remaining === 'number' ? remaining.toLocaleString() : '—'}</td><td><span className="muted">{account.expires_at ? new Date(account.expires_at).toLocaleDateString('zh-CN') : '未提供'}</span></td><td className="row-menu"><AccountRowActions account={account} csrf={csrf} onRefresh={onRefresh} onFeedback={showFeedback}/></td></tr>
        {feedback?.key === key && <tr className={`account-feedback ${feedback.error ? 'error' : ''}`}><td colSpan="6">{feedback.message}</td></tr>}
      </React.Fragment>
    })}</DataTable>
  </section>
}

function Connections({ data, onAdd }) {
  const rows = data?.connections || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">自定义服务</span><h2>兼容 OpenAI 的接口</h2><p className="muted">使用 Base URL 与 API Key 接入其他服务。</p></div><button className="button primary" onClick={onAdd}><Plus size={15}/>添加服务</button></div><DataTable columns={['服务','模型前缀','状态','模型数','']} empty={!rows.length && '暂未添加自定义服务'}>{rows.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.base_url}</small></td><td><code>{item.id}/</code></td><td><StatusBadge status={item.enabled ? 'ready' : 'paused'}/></td><td className="tabular">{item.models?.length || 0}</td><td className="row-menu"><button aria-label="服务操作">•••</button></td></tr>)}</DataTable></section>
}

function Models({ data, onNavigate }) {
  const [query, setQuery] = useState('')
  const rows = (data?.models || []).filter(m => m.id.toLowerCase().includes(query.toLowerCase()))
  return <section className="content-card"><div className="section-head"><div><span className="section-label">模型目录</span><h2>{data?.models?.length || 0} 个可用模型</h2></div><label className="search-box"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型" /></label></div><DataTable columns={['模型 ID','来源','兼容接口','']} empty={!rows.length && '没有匹配的模型'}>{rows.map(m => <tr key={m.id}><td><code>{m.id}</code></td><td><span className="provider-badge">{providerName(m.owned_by)}</span></td><td className="muted">{m.owned_by === 'codebuddy' ? 'Chat · Responses · Messages' : 'Chat Completions'}</td><td><button className="link-button" onClick={() => onNavigate('test')}>测试</button></td></tr>)}</DataTable></section>
}

function Keys({ data, onAdd }) {
  const rows = data?.keys || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">访问控制</span><h2>API 密钥</h2></div><button className="button primary" onClick={onAdd}><Plus size={15}/>创建密钥</button></div><DataTable columns={['名称','前缀','创建时间','']} empty={!rows.length && '暂未创建 API 密钥'}>{rows.map(k => <tr key={k.id}><td><strong>{k.name}</strong></td><td><code>{k.prefix || '已隐藏'}…</code></td><td className="muted">{k.created ? new Date(k.created * 1000).toLocaleDateString('zh-CN') : '—'}</td><td className="row-menu"><button>•••</button></td></tr>)}</DataTable></section>
}

function RouteDialog({ data, csrf, onClose, onSaved }) {
  const models = (data?.models || []).filter(model => !model.id.startsWith('route/'))
  const [form, setForm] = useState({ id: '', name: '', strategy: 'priority', retries: 2, cooldown_seconds: 300, targets: [] })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const toggle = model => update('targets', form.targets.includes(model) ? form.targets.filter(item => item !== model) : [...form.targets, model])
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { await request('unified/routes', { method: 'POST', body: form, csrf }); await onSaved() }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title="创建智能路由" subtitle="模型故障转移" onClose={onClose}><form onSubmit={save}>
    <div className="field-grid"><div><label>路由名称</label><input required maxLength="60" value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：稳定编程模型"/></div><div><label>调用前缀</label><input required pattern="[a-z][a-z0-9_-]{0,39}" value={form.id} onChange={e => update('id', e.target.value)} placeholder="code-stable"/></div></div>
    <div className="field-grid"><div><label>选择策略</label><select value={form.strategy} onChange={e => update('strategy', e.target.value)}><option value="priority">按顺序优先</option><option value="round_robin">轮询分配</option><option value="latency">优先低延迟</option></select></div><div><label>失败后最多切换</label><input type="number" min="0" max="10" value={form.retries} onChange={e => update('retries', Number(e.target.value))}/></div></div>
    <label>失败冷却时间（秒）</label><input type="number" min="10" max="86400" value={form.cooldown_seconds} onChange={e => update('cooldown_seconds', Number(e.target.value))}/>
    <label>目标模型（按选择顺序）</label><div className="model-picker">{models.map(model => <label key={model.id} className={form.targets.includes(model.id) ? 'selected' : ''}><input type="checkbox" checked={form.targets.includes(model.id)} onChange={() => toggle(model.id)}/><code>{model.id}</code></label>)}</div>
    <p className="field-help">客户端使用 <code>route/{form.id || '路由前缀'}</code>。上游失败时会按策略自动尝试下一个目标。</p>
    {error && <p className="form-error">{error}</p>}<button className="button primary wide" disabled={busy || !form.targets.length}>{busy ? '正在保存…' : '创建路由'}</button>
  </form></Modal>
}

function Routes({ data, csrf, onRefresh, onAdd }) {
  const rows = data?.routes || []
  const [message, setMessage] = useState('')
  async function remove(id) {
    if (!window.confirm(`删除路由 route/${id}？`)) return
    try { await request(`unified/routes/${encodeURIComponent(id)}`, { method: 'DELETE', body: {}, csrf }); setMessage('路由已删除'); await onRefresh() }
    catch (err) { setMessage(err.message) }
  }
  return <div className="stack">
    {message && <div className="result-banner">{message}</div>}
    <section className="content-card"><div className="section-head"><div><span className="section-label">统一入口</span><h2>智能路由</h2><p className="muted">为多个真实模型创建一个稳定别名，并在失败时自动切换。</p></div><button className="button primary" onClick={onAdd}><Plus size={15}/>创建路由</button></div>
      <DataTable columns={['路由','策略','目标模型','健康状态','']} empty={!rows.length && '暂未创建智能路由'}>{rows.map(route => {
        const cooling = Object.values(route.health || {}).filter(item => item.cooldown_until * 1000 > Date.now()).length
        return <tr key={route.id}><td><strong>{route.name}</strong><small><code>route/{route.id}</code></small></td><td>{({priority:'顺序优先',round_robin:'轮询',latency:'低延迟'}[route.strategy])}</td><td><strong>{route.targets.length} 个</strong><small>{route.targets.join(' → ')}</small></td><td><StatusBadge status={cooling ? 'cooling' : 'ready'}/><small>{cooling ? `${cooling} 个目标冷却中` : '全部可参与路由'}</small></td><td className="row-menu"><button className="danger-icon" onClick={() => remove(route.id)} aria-label="删除路由"><Trash2 size={14}/></button></td></tr>
      })}</DataTable>
    </section>
  </div>
}

function Logs({ csrf }) {
  const [state, setState] = useState({ items: [], total: 0, summary: {} })
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  async function load() {
    setLoading(true)
    try { setState(await request(`unified/logs?limit=100&model=${encodeURIComponent(filter)}`)) } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  async function clear() {
    if (!window.confirm('清空全部调用记录？')) return
    await request('unified/logs', { method: 'DELETE', body: {}, csrf }); await load()
  }
  const summary = state.summary || {}
  return <div className="stack">
    <section className="summary-grid compact-summary">
      {[['24 小时请求', summary.count || 0], ['成功率', summary.success_rate == null ? '—' : `${summary.success_rate}%`], ['平均耗时', summary.avg_duration_ms ? `${Math.round(summary.avg_duration_ms)} ms` : '—'], ['推理 Token', (summary.reasoning_tokens || 0).toLocaleString()]].map(([label,value]) => <article className="summary-card" key={label}><div><strong>{value}</strong><span>{label}</span></div></article>)}
    </section>
    <section className="content-card"><div className="section-head"><div><span className="section-label">最近 90 天</span><h2>调用记录</h2><p className="muted">不保存提示词、回复正文或密钥。</p></div><div className="head-actions"><button className="button secondary" onClick={clear}><Trash2 size={14}/>清空</button><button className="button secondary" onClick={load}><RefreshCw size={14} className={loading ? 'spin' : ''}/>刷新</button></div></div>
      <div className="toolbar"><label className="search-box"><Search size={15}/><input value={filter} onChange={e => setFilter(e.target.value)} onKeyDown={e => e.key === 'Enter' && load()} placeholder="筛选模型"/></label><span className="muted">共 {state.total || 0} 条</span></div>
      <DataTable columns={['时间','模型','实际目标','结果','耗时','Token']} empty={!state.items.length && '暂无调用记录'}>{state.items.map(item => <tr key={item.id}><td className="muted">{new Date(item.created * 1000).toLocaleString('zh-CN')}</td><td><code>{item.model || '—'}</code></td><td><small>{item.resolved_model || '—'}</small></td><td><StatusBadge status={item.ok ? 'ready' : 'error'}/><small>{item.status || item.outcome}</small></td><td className="tabular">{item.duration_ms.toLocaleString()} ms</td><td><strong>{(item.prompt_tokens + item.completion_tokens).toLocaleString()}</strong><small>推理 {item.reasoning_tokens.toLocaleString()}</small></td></tr>)}</DataTable>
    </section>
  </div>
}

function Automation({ data, csrf, onRefresh }) {
  const initial = data?.automation || {}
  const [form, setForm] = useState(initial)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  useEffect(() => setForm(initial), [data])
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  async function save(event) {
    event.preventDefault(); setBusy('save'); setMessage('')
    try { await request('unified/automation', { method: 'PATCH', body: form, csrf }); setMessage('自动任务设置已保存'); await onRefresh() }
    catch (err) { setMessage(err.message) } finally { setBusy('') }
  }
  async function run(action) {
    setBusy(action); setMessage('')
    try { const result = await request(`unified/automation/run/${action}`, { method: 'POST', body: {}, csrf }); const sum = result.result?.summary || {}; setMessage(`${action === 'checkin' ? '签到' : '刷新'}完成：成功 ${sum.succeeded || 0}，失败 ${sum.failed || 0}`); await onRefresh() }
    catch (err) { setMessage(err.message) } finally { setBusy('') }
  }
  return <div className="settings-grid">
    <form className="content-card" onSubmit={save}><span className="section-label">计划任务</span><h2>签到与余额刷新</h2>
      <label className="switch-row"><span><strong>每日自动签到</strong><small>按服务器所在时区执行</small></span><input type="checkbox" checked={Boolean(form.auto_checkin)} onChange={e => update('auto_checkin', e.target.checked)}/></label>
      <label>每日签到时间</label><input type="time" value={form.checkin_time || '09:00'} onChange={e => update('checkin_time', e.target.value)}/>
      <label className="switch-row"><span><strong>自动刷新状态与余额</strong><small>同步凭据状态并查询最新额度</small></span><input type="checkbox" checked={Boolean(form.auto_refresh)} onChange={e => update('auto_refresh', e.target.checked)}/></label>
      <label>刷新间隔（分钟）</label><input type="number" min="5" max="1440" value={form.refresh_minutes || 30} onChange={e => update('refresh_minutes', Number(e.target.value))}/>
      <button className="button primary wide" disabled={Boolean(busy)}><Save size={15}/>{busy === 'save' ? '正在保存…' : '保存设置'}</button>
    </form>
    <section className="content-card"><span className="section-label">告警</span><h2>Webhook 通知</h2><label>Webhook 地址</label><input type="url" value={form.webhook_url || ''} onChange={e => update('webhook_url', e.target.value)} placeholder="https://example.com/webhook"/><label>低余额阈值</label><input type="number" min="0" step="0.01" value={form.low_balance || 0} onChange={e => update('low_balance', Number(e.target.value))}/><label className="switch-row"><span><strong>任务失败时通知</strong><small>只发送账号 ID、状态和余额，不发送凭据</small></span><input type="checkbox" checked={form.notify_failures !== false} onChange={e => update('notify_failures', e.target.checked)}/></label><div className="inline-actions"><button className="button secondary" onClick={() => run('checkin')} disabled={Boolean(busy)}><Play size={14}/>{busy === 'checkin' ? '执行中…' : '立即签到'}</button><button className="button secondary" onClick={() => run('refresh')} disabled={Boolean(busy)}><RefreshCw size={14} className={busy === 'refresh' ? 'spin' : ''}/>{busy === 'refresh' ? '执行中…' : '立即刷新'}</button></div></section>
    {message && <div className="result-banner settings-message">{message}</div>}
    <section className="content-card settings-wide"><div className="section-head"><div><span className="section-label">执行历史</span><h2>最近自动任务</h2></div></div><DataTable columns={['时间','任务','触发方式','结果']} empty={!data?.automation_history?.length && '还没有执行记录'}>{(data?.automation_history || []).map((item,index) => <tr key={`${item.time}-${index}`}><td>{new Date(item.time * 1000).toLocaleString('zh-CN')}</td><td>{item.action === 'checkin' ? '签到' : '刷新'}</td><td>{item.manual ? '手动' : '自动'}</td><td><StatusBadge status={item.ok ? 'ready' : 'error'}/><small>{item.summary ? `成功 ${item.summary.succeeded || 0} / 失败 ${item.summary.failed || 0}` : item.error}</small></td></tr>)}</DataTable></section>
  </div>
}

function Backup({ csrf }) {
  const [password, setPassword] = useState('')
  const [includeLogs, setIncludeLogs] = useState(false)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  async function exportData() {
    setBusy('export'); setMessage('')
    try {
      const response = await fetch('/admin/api/unified/backup/export', { method: 'POST', credentials: 'same-origin', headers: {'Content-Type':'application/json','X-CSRF-Token':csrf}, body: JSON.stringify({ password, include_logs: includeLogs }) })
      if (!response.ok) { const value = await response.json().catch(() => ({})); throw new Error(value.detail || '导出失败') }
      const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = `unified2api-${new Date().toISOString().slice(0,10)}.ubak`; link.click(); URL.revokeObjectURL(url); setMessage('加密备份已下载')
    } catch (err) { setMessage(err.message) } finally { setBusy('') }
  }
  async function importData() {
    if (!file || !window.confirm('恢复会替换当前账号和设置，服务随后会自动重启。继续吗？')) return
    setBusy('import'); setMessage('正在校验并恢复备份…')
    try {
      const dataUrl = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result); reader.onerror = reject; reader.readAsDataURL(file) })
      const result = await request('unified/backup/import', { method: 'POST', body: { password, data: String(dataUrl).split(',')[1] }, csrf })
      setMessage(result.restarting ? '恢复成功，服务正在重启，请稍后刷新页面' : '恢复成功，请重启服务后使用')
    } catch (err) { setMessage(err.message) } finally { setBusy('') }
  }
  return <div className="settings-grid"><section className="content-card"><span className="section-label">导出</span><h2>创建加密备份</h2><p className="muted backup-copy">包含 TRAE、CodeBuddy、MonkeyCode 账号、自定义服务、路由和自动任务设置。</p><label>备份密码</label><input type="password" minLength="8" value={password} onChange={e => setPassword(e.target.value)} placeholder="至少 8 个字符"/><label className="switch-row"><span><strong>包含调用记录</strong><small>可能明显增大备份文件</small></span><input type="checkbox" checked={includeLogs} onChange={e => setIncludeLogs(e.target.checked)}/></label><button className="button primary wide" onClick={exportData} disabled={busy || password.length < 8}><Download size={15}/>{busy === 'export' ? '正在打包…' : '下载加密备份'}</button></section>
    <section className="content-card"><span className="section-label">恢复</span><h2>导入备份</h2><p className="muted backup-copy">仅接受 Unified2API 的 <code>.ubak</code> 加密文件。恢复成功后容器自动重启。</p><label>备份文件</label><input type="file" accept=".ubak,application/octet-stream" onChange={e => setFile(e.target.files?.[0] || null)}/><label>备份密码</label><input type="password" minLength="8" value={password} onChange={e => setPassword(e.target.value)} placeholder="创建备份时使用的密码"/><button className="button secondary wide" onClick={importData} disabled={busy || !file || password.length < 8}><Upload size={15}/>{busy === 'import' ? '正在恢复…' : '恢复并重启服务'}</button></section>
    {message && <div className="result-banner settings-message">{message}</div>}
  </div>
}

function TestPanel({ data, csrf }) {
  const [model, setModel] = useState('')
  const [message, setMessage] = useState('请只回复：连接成功')
  const [output, setOutput] = useState('')
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setBusy(true); setFailed(false); setOutput('正在等待上游响应…')
    try {
      const result = await request('unified/test', { method: 'POST', body: { model, message }, csrf })
      setFailed(!result.ok)
      setOutput(result.ok ? result.answer : result.error || '调用失败，上游未提供错误详情')
    } catch (err) {
      setFailed(true)
      setOutput(err.message)
    } finally { setBusy(false) }
  }
  return <div className="test-layout">
    <form className="content-card" onSubmit={submit}>
      <span className="section-label">请求</span><h2>调用测试</h2>
      <label htmlFor="test-model">模型</label>
      <select id="test-model" value={model} onChange={e => setModel(e.target.value)} required>
        <option value="" disabled>选择模型</option>
        {(data?.models || []).map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
      </select>
      <label htmlFor="test-message">消息</label>
      <textarea id="test-message" rows="7" value={message} onChange={e => setMessage(e.target.value)} maxLength="16000" required/>
      <button className="button primary wide" disabled={busy}>{busy ? '正在调用…' : '发送请求'}</button>
    </form>
    <section className="content-card response-panel">
      <span className="section-label">响应</span><h2>{failed ? '调用失败' : '模型回复'}</h2>
      {output ? <pre className={`response-output ${failed ? 'form-error' : ''}`} role={failed ? 'alert' : 'status'}>{output}</pre> : <Empty>发送请求后，结果会显示在这里</Empty>}
    </section>
  </div>
}

function App() {
  const [csrf, setCsrf] = useState('')
  const [data, setData] = useState(null)
  const [page, setPage] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [authenticated, setAuthenticated] = useState(true)
  const [drawer, setDrawer] = useState(false)
  const [dialog, setDialog] = useState(null)

  async function load() {
    setLoading(true)
    try {
      const [overview, session] = await Promise.all([request('unified/overview'), request('session')])
      setData(overview); setCsrf(session.csrf); setAuthenticated(true)
    }
    catch (err) { if (err.status === 401) setAuthenticated(false) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  async function login(key) { const result = await request('login', { method: 'POST', body: { key } }); setCsrf(result.csrf); setAuthenticated(true); await load() }
  async function logout() { await request('logout', { method: 'POST', body: {}, csrf }); setData(null); setAuthenticated(false) }
  function navigate(next) { setPage(next); setDrawer(false) }
  async function saved(keepOpen = false) { await load(); if (!keepOpen) setDialog(null) }

  if (loading && !data) return <div className="boot"><RefreshCw className="spin" size={20}/><span>正在连接控制台</span></div>
  if (!authenticated) return <Login onLogin={login}/>
  const [title, description] = pageMeta[page]
  const pages = {
    overview: <Overview data={data} onNavigate={navigate}/>,
    accounts: <Accounts data={data} csrf={csrf} onRefresh={load} onAdd={() => setDialog('account')}/>,
    connections: <Connections data={data} onAdd={() => setDialog('connection')}/>,
    models: <Models data={data} onNavigate={navigate}/>,
    keys: <Keys data={data} onAdd={() => setDialog('key')}/>,
    test: <TestPanel data={data} csrf={csrf}/>,
    routes: <Routes data={data} csrf={csrf} onRefresh={load} onAdd={() => setDialog('route')}/>,
    logs: <Logs csrf={csrf}/>,
    automation: <Automation data={data} csrf={csrf} onRefresh={load}/>,
    backup: <Backup csrf={csrf}/>,
  }
  const content = pages[page]

  return <div className="app-shell">
    {drawer && (
      <button className="drawer-backdrop" aria-label="关闭导航" onClick={() => setDrawer(false)}/>
    )}
    <aside className={`sidebar glass-panel ${drawer ? 'open' : ''}`}>
      <div className="sidebar-top"><a className="wordmark" href="/admin/">Unified</a><button className="mobile-close" onClick={() => setDrawer(false)}><X size={18}/></button></div>
      <div className="environment"><span><i/>服务在线</span><small>本地网关</small></div>
      <nav>{navGroups.map(group => <div className="nav-group" key={group.label}><span>{group.label}</span>{group.items.map(([id, Icon]) => <button key={id} className={page === id ? 'active' : ''} onClick={() => navigate(id)}><Icon size={17}/>{pageMeta[id][0]}</button>)}</div>)}</nav>
      <div className="sidebar-bottom"><button><Settings2 size={17}/>设置</button><button onClick={logout}><LogOut size={17}/>退出登录</button></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><button className="menu-button" onClick={() => setDrawer(true)}><Menu size={19}/></button><div><span>Unified / {title}</span></div><div className="top-actions"><span className="live-status"><i/>运行中</span><button className="icon-action" onClick={load} aria-label="刷新"><RefreshCw size={16} className={loading ? 'spin' : ''}/></button></div></header>
      <main className="page"><header className="page-heading"><div><h1>{title}</h1><p>{description}</p></div>{page === 'accounts' && <button className="button primary" onClick={() => setDialog('account')}><Plus size={15}/>添加账号</button>}</header>{content}</main>
    </div>
    {dialog === 'account' && (
      <AccountDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
    {dialog === 'connection' && (
      <ConnectionDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
    {dialog === 'key' && (
      <KeyDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved(true)}/>
    )}
    {dialog === 'route' && (
      <RouteDialog data={data} csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
  </div>
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
