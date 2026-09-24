import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import {
  Activity, Blocks, Bot, Check, ChevronDown, CircleGauge, Copy, Database, ExternalLink, Moon, Sun,
  Archive, Clock, Download, FileText, FlaskConical, KeyRound, LogOut, Menu, Network,
  Pencil, Play, Plus, RefreshCw, Save, Search, Server, Settings2, Trash2, Upload, Users,
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
  usage: ['用量统计', '查看每日、每周和累计 Token 消耗'],
  settings: ['设置', '调整外观、统计保留时间和自动刷新'],
}

const navGroups = [
  { label: '工作台', items: [['overview', CircleGauge], ['accounts', Users], ['connections', Server], ['models', Bot]] },
  { label: '开发', items: [['keys', KeyRound], ['test', FlaskConical], ['routes', Network]] },
  { label: '运维', items: [['usage', Activity], ['logs', FileText], ['automation', Clock], ['backup', Archive]] },
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

function ConnectionDialog({ csrf, initialConnection, onClose, onSaved }) {
  const [form, setForm] = useState(() => initialConnection ? {
    name: initialConnection.name, id: initialConnection.id, base_url: initialConnection.base_url,
    key: '', models: (initialConnection.models || []).join('\n'), enabled: initialConnection.enabled !== false,
  } : { name: '', id: '', base_url: '', key: '', models: '', enabled: true })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const body = () => ({ name: form.name.trim(), id: form.id.trim(), base_url: form.base_url.trim(), key: form.key.trim(), models: form.models.split(/\r?\n/).map(v => v.trim()).filter(Boolean), enabled: form.enabled, ...(initialConnection ? { existing_id: initialConnection.id } : {}) })
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
      const path = initialConnection ? `unified/connections/${encodeURIComponent(initialConnection.id)}` : 'unified/connections'
      await request(path, { method: initialConnection ? 'PATCH' : 'POST', body: value, csrf })
      await onSaved()
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title={initialConnection ? '编辑自定义服务' : '添加自定义服务'} subtitle="OpenAI 兼容接口" onClose={onClose}><form onSubmit={save}>
    <div className="field-grid"><div><label>服务名称</label><input required maxLength="60" value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：我的模型服务"/></div><div><label>模型前缀</label><input required readOnly={Boolean(initialConnection)} pattern="[a-z][a-z0-9_-]{0,39}" value={form.id} onChange={e => update('id', e.target.value)} placeholder="myapi"/></div></div>
    {initialConnection && <p className="field-help">模型前缀保持不变，现有客户端仍可使用 <code>{form.id}/</code>。</p>}
    <label>Base URL</label><input type="url" required value={form.base_url} onChange={e => update('base_url', e.target.value)} placeholder="https://api.example.com/v1"/>
    <label>API Key</label><input type="password" required={!initialConnection} value={form.key} onChange={e => update('key', e.target.value)} autoComplete="new-password" placeholder={initialConnection ? '留空则保持当前 Key' : '服务商提供的 Key'}/>
    <label>模型名称（每行一个）</label><textarea rows="5" value={form.models} onChange={e => update('models', e.target.value)} placeholder="model-name"/>
    <button className="button secondary wide" type="button" onClick={discover} disabled={busy}>读取模型列表</button>
    <label className="switch-row"><span><strong>启用服务</strong><small>停用后模型将从目录中隐藏</small></span><input type="checkbox" checked={form.enabled} onChange={e => update('enabled', e.target.checked)}/></label>
    {error && <p className="form-error" role="alert">{error}</p>}
    <button className="button primary wide" disabled={busy}>{busy ? '正在保存…' : initialConnection ? '保存修改' : '保存服务'}</button>
  </form></Modal>
}

function KeyDialog({ csrf, onClose, onSaved }) {
  const [name, setName] = useState('')
  const [created, setCreated] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try { const result = await request('keys', { method: 'POST', body: { name }, csrf }); setCreated(result.key); await onSaved(true) }
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

function ConnectionRowActions({ connection, csrf, onRefresh, onEdit, onFeedback }) {
  const [more, setMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 })
  const moreButton = useRef(null)
  const menu = useRef(null)

  useEffect(() => {
    if (!more) return undefined
    function close(event) {
      if (!moreButton.current?.contains(event.target) && !menu.current?.contains(event.target)) setMore(false)
    }
    function closeForViewportChange() { setMore(false) }
    function closeOnEscape(event) { if (event.key === 'Escape') setMore(false) }
    window.addEventListener('pointerdown', close)
    window.addEventListener('resize', closeForViewportChange)
    window.addEventListener('scroll', closeForViewportChange, true)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('resize', closeForViewportChange)
      window.removeEventListener('scroll', closeForViewportChange, true)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [more])

  function toggleMore() {
    if (!more && moreButton.current) {
      const rect = moreButton.current.getBoundingClientRect()
      setMenuPosition({ top: Math.min(rect.bottom + 7, window.innerHeight - 128), left: Math.max(10, rect.right - 142) })
    }
    setMore(value => !value)
  }

  async function toggleEnabled() {
    setMore(false); setBusy(true)
    try {
      await request(`unified/connections/${encodeURIComponent(connection.id)}`, { method: 'PATCH', body: { enabled: !connection.enabled }, csrf })
      onFeedback(connection.enabled ? '服务已停用' : '服务已启用', false)
      await onRefresh()
    } catch (err) { onFeedback(err.message, true) }
    finally { setBusy(false) }
  }

  async function remove() {
    setMore(false)
    if (!window.confirm(`确定删除服务“${connection.name}”吗？引用它的智能路由可能无法使用。`)) return
    setBusy(true)
    try {
      await request(`unified/connections/${encodeURIComponent(connection.id)}`, { method: 'DELETE', csrf })
      onFeedback('服务已删除', false)
      await onRefresh()
    } catch (err) { onFeedback(err.message, true) }
    finally { setBusy(false) }
  }

  return <>
    <button ref={moreButton} className={`dots-action ${more ? 'active' : ''}`} onClick={toggleMore} aria-label={`管理服务 ${connection.name}`} aria-expanded={more} aria-haspopup="menu" disabled={busy}>•••</button>
    {more && createPortal(<div ref={menu} className="account-popover connection-popover" style={menuPosition} role="menu">
      <button role="menuitem" onClick={() => { setMore(false); onEdit(connection) }}>编辑服务</button>
      <button role="menuitem" onClick={toggleEnabled}>{connection.enabled ? '停用服务' : '启用服务'}</button>
      <button role="menuitem" className="danger-text" onClick={remove}>删除服务</button>
    </div>, document.body)}
  </>
}

function Connections({ data, csrf, onRefresh, onAdd, onEdit }) {
  const [feedback, setFeedback] = useState(null)
  const rows = data?.connections || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">自定义服务</span><h2>兼容 OpenAI 的接口</h2><p className="muted">使用 Base URL 与 API Key 接入其他服务。</p></div><button className="button primary" onClick={onAdd}><Plus size={15}/>添加服务</button></div>
    {feedback && <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">{feedback.message}</div>}
    <DataTable columns={['服务','模型前缀','状态','模型数','操作']} empty={!rows.length && '暂未添加自定义服务'}>{rows.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.base_url}</small></td><td><code>{item.id}/</code></td><td><StatusBadge status={item.enabled ? 'ready' : 'paused'}/></td><td className="tabular">{item.models?.length || 0}</td><td className="row-menu"><ConnectionRowActions connection={item} csrf={csrf} onRefresh={onRefresh} onEdit={onEdit} onFeedback={(message, error) => setFeedback({ message, error })}/></td></tr>)}</DataTable>
  </section>
}

function Models({ data, onNavigate }) {
  const [query, setQuery] = useState('')
  const rows = (data?.models || []).filter(m => m.id.toLowerCase().includes(query.toLowerCase()))
  return <section className="content-card"><div className="section-head"><div><span className="section-label">模型目录</span><h2>{data?.models?.length || 0} 个可用模型</h2></div><label className="search-box"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型" /></label></div><DataTable columns={['模型 ID','来源','兼容接口','']} empty={!rows.length && '没有匹配的模型'}>{rows.map(m => <tr key={m.id}><td><code>{m.id}</code></td><td><span className="provider-badge">{providerName(m.owned_by)}</span></td><td className="muted">{m.owned_by === 'codebuddy' ? 'Chat · Responses · Messages' : 'Chat Completions'}</td><td><button className="link-button" onClick={() => onNavigate('test')}>测试</button></td></tr>)}</DataTable></section>
}

function KeyRowActions({ apiKey, csrf, onRefresh, onFeedback }) {
  const [more, setMore] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(apiKey.name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 })
  const moreButton = useRef(null)
  const menu = useRef(null)

  useEffect(() => {
    if (!more) return undefined
    function close(event) {
      if (!moreButton.current?.contains(event.target) && !menu.current?.contains(event.target)) setMore(false)
    }
    function closeForViewportChange() { setMore(false) }
    function closeOnEscape(event) { if (event.key === 'Escape') setMore(false) }
    window.addEventListener('pointerdown', close)
    window.addEventListener('resize', closeForViewportChange)
    window.addEventListener('scroll', closeForViewportChange, true)
    window.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('pointerdown', close)
      window.removeEventListener('resize', closeForViewportChange)
      window.removeEventListener('scroll', closeForViewportChange, true)
      window.removeEventListener('keydown', closeOnEscape)
    }
  }, [more])

  function toggleMore() {
    if (!more && moreButton.current) {
      const rect = moreButton.current.getBoundingClientRect()
      setMenuPosition({ top: Math.min(rect.bottom + 7, window.innerHeight - 90), left: Math.max(10, rect.right - 126) })
    }
    setMore(value => !value)
  }

  async function rename(event) {
    event.preventDefault()
    setBusy(true); setError('')
    try {
      await request(`keys/${encodeURIComponent(apiKey.id)}`, { method: 'PATCH', body: { name: name.trim() }, csrf })
      setRenaming(false)
      onFeedback('密钥名称已更新', false)
      await onRefresh()
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  async function remove() {
    setMore(false)
    if (!window.confirm(`确定撤销密钥“${apiKey.name}”吗？使用它的客户端将立即无法访问 API。`)) return
    setBusy(true)
    try {
      await request(`keys/${encodeURIComponent(apiKey.id)}`, { method: 'DELETE', csrf })
      onFeedback('密钥已撤销', false)
      await onRefresh()
    } catch (err) { onFeedback(err.message, true) }
    finally { setBusy(false) }
  }

  return <>
    <button ref={moreButton} className={`dots-action ${more ? 'active' : ''}`} onClick={toggleMore} aria-label={`管理密钥 ${apiKey.name}`} aria-expanded={more} aria-haspopup="menu" disabled={busy}>•••</button>
    {more && createPortal(<div ref={menu} className="account-popover" style={menuPosition} role="menu">
      <button role="menuitem" onClick={() => { setMore(false); setName(apiKey.name); setError(''); setRenaming(true) }}>重命名</button>
      <button role="menuitem" className="danger-text" onClick={remove}>撤销密钥</button>
    </div>, document.body)}
    {renaming && createPortal(<Modal title="重命名密钥" subtitle="客户端访问" onClose={() => setRenaming(false)}>
      <form onSubmit={rename}><label htmlFor={`key-name-${apiKey.id}`}>密钥名称</label><input id={`key-name-${apiKey.id}`} autoFocus required maxLength="60" value={name} onChange={event => setName(event.target.value)}/>
        {error && <p className="form-error" role="alert">{error}</p>}
        <button className="button primary wide" disabled={busy}>{busy ? '正在保存…' : '保存名称'}</button>
      </form>
    </Modal>, document.body)}
  </>
}

function Keys({ data, csrf, onRefresh, onAdd }) {
  const [feedback, setFeedback] = useState(null)
  const rows = data?.keys || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">访问控制</span><h2>API 密钥</h2></div><button className="button primary" onClick={onAdd}><Plus size={15}/>创建密钥</button></div>
    {feedback && <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">{feedback.message}</div>}
    <DataTable columns={['名称','前缀','创建时间','操作']} empty={!rows.length && '暂未创建 API 密钥'}>{rows.map(k => <tr key={k.id}><td><strong>{k.name}</strong></td><td><code>{k.hint || (k.prefix ? `${k.prefix}…` : '已隐藏')}</code></td><td className="muted">{k.created ? new Date(k.created * 1000).toLocaleDateString('zh-CN') : '—'}</td><td className="row-menu"><KeyRowActions apiKey={k} csrf={csrf} onRefresh={onRefresh} onFeedback={(message, error) => setFeedback({ message, error })}/></td></tr>)}</DataTable>
  </section>
}

function RouteDialog({ data, csrf, initialRoute, onClose, onSaved }) {
  const models = (data?.models || []).filter(model => !model.id.startsWith('route/'))
  const [modelQuery, setModelQuery] = useState('')
  const visibleModels = models.filter(model => model.id.toLowerCase().includes(modelQuery.trim().toLowerCase()))
  const [form, setForm] = useState(() => initialRoute ? {
    id: initialRoute.id, name: initialRoute.name, strategy: initialRoute.strategy,
    retries: initialRoute.retries, cooldown_seconds: initialRoute.cooldown_seconds,
    targets: [...initialRoute.targets], enabled: initialRoute.enabled !== false,
  } : { id: '', name: '', strategy: 'priority', retries: 2, cooldown_seconds: 300, targets: [], enabled: true })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const update = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const toggle = model => update('targets', form.targets.includes(model) ? form.targets.filter(item => item !== model) : [...form.targets, model])
  const moveTarget = (index, direction) => setForm(current => {
    const next = [...current.targets]
    const other = index + direction
    if (other < 0 || other >= next.length) return current
    const moved = next[index]
    next[index] = next[other]
    next[other] = moved
    return { ...current, targets: next }
  })
  async function save(event) {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const path = initialRoute ? `unified/routes/${encodeURIComponent(initialRoute.id)}` : 'unified/routes'
      await request(path, { method: initialRoute ? 'PATCH' : 'POST', body: form, csrf })
      await onSaved()
    }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <Modal title={initialRoute ? '编辑智能路由' : '创建智能路由'} subtitle="模型故障转移" onClose={onClose}><form onSubmit={save}>
    <div className="field-grid"><div><label>路由名称</label><input required maxLength="60" value={form.name} onChange={e => update('name', e.target.value)} placeholder="例如：稳定编程模型"/></div><div><label>调用前缀</label><input required readOnly={Boolean(initialRoute)} pattern="[a-z][a-z0-9_.-]{0,39}" title="小写字母开头，可包含数字、点号、下划线和短横线" value={form.id} onChange={e => update('id', e.target.value)} placeholder="glm-5.3"/></div></div>
    {initialRoute && <p className="field-help">调用前缀保持不变，现有客户端可以继续使用 <code>route/{form.id}</code>。</p>}
    <div className="field-grid"><div><label>选择策略</label><select value={form.strategy} onChange={e => update('strategy', e.target.value)}><option value="priority">按顺序优先</option><option value="round_robin">轮询分配</option><option value="latency">优先低延迟</option></select></div><div><label>失败后最多切换</label><input type="number" min="0" max="10" value={form.retries} onChange={e => update('retries', Number(e.target.value))}/></div></div>
    <label>失败冷却时间（秒）</label><input type="number" min="10" max="86400" value={form.cooldown_seconds} onChange={e => update('cooldown_seconds', Number(e.target.value))}/>
    <label htmlFor="route-model-search">目标模型（按选择顺序）</label>
    <div className="model-picker-search"><Search size={15}/><input id="route-model-search" type="search" value={modelQuery} onChange={event => setModelQuery(event.target.value)} placeholder="输入模型名称快速筛选" autoComplete="off"/></div>
    <div className="model-picker">{visibleModels.length ? visibleModels.map(model => <label key={model.id} className={form.targets.includes(model.id) ? 'selected' : ''}><input type="checkbox" checked={form.targets.includes(model.id)} onChange={() => toggle(model.id)}/><code>{model.id}</code></label>) : <p className="model-picker-empty">没有匹配的模型</p>}</div>
    {form.targets.length > 0 && <div className="route-target-order" aria-label="目标模型顺序">{form.targets.map((target, index) => <div key={target}><span>{index + 1}</span><code title={target}>{target}</code><button type="button" onClick={() => moveTarget(index, -1)} disabled={index === 0} aria-label={`上移 ${target}`}>↑</button><button type="button" onClick={() => moveTarget(index, 1)} disabled={index === form.targets.length - 1} aria-label={`下移 ${target}`}>↓</button></div>)}</div>}
    <label className="switch-row"><span><strong>启用路由</strong><small>停用后，客户端将无法调用这个路由</small></span><input type="checkbox" checked={form.enabled} onChange={e => update('enabled', e.target.checked)}/></label>
    <p className="field-help">客户端使用 <code>route/{form.id || '路由前缀'}</code>。上游失败时会按策略自动尝试下一个目标。</p>
    {error && <p className="form-error" role="alert">{error}</p>}<button className="button primary wide" disabled={busy || !form.targets.length}>{busy ? '正在保存…' : initialRoute ? '保存修改' : '创建路由'}</button>
  </form></Modal>
}

function Routes({ data, csrf, onRefresh, onAdd, onEdit }) {
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
        return <tr key={route.id}><td><strong>{route.name}</strong><small><code>route/{route.id}</code></small></td><td>{({priority:'顺序优先',round_robin:'轮询',latency:'低延迟'}[route.strategy])}</td><td><strong>{route.targets.length} 个</strong><small>{route.targets.join(' → ')}</small></td><td><StatusBadge status={route.enabled === false ? 'paused' : cooling ? 'cooling' : 'ready'}/><small>{route.enabled === false ? '路由已停用' : cooling ? `${cooling} 个目标冷却中` : '全部可参与路由'}</small></td><td className="row-menu"><div className="route-row-actions"><button type="button" onClick={() => onEdit(route)} aria-label={`编辑路由 ${route.name}`} title="编辑路由"><Pencil size={14}/></button><button type="button" className="danger-icon" onClick={() => remove(route.id)} aria-label={`删除路由 ${route.name}`} title="删除路由"><Trash2 size={14}/></button></div></td></tr>
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

const tokenCount = value => Number(value || 0)
const exactCount = value => tokenCount(value).toLocaleString('zh-CN')
const tokenText = value => {
  const count = tokenCount(value)
  if (!count) return '0'
  if (count < 10000) return '<0.01 M'
  const large = count >= 100000000
  const amount = count / (large ? 100000000 : 1000000)
  return `${amount.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${large ? '亿' : 'M'}`
}
const totalTokens = row => tokenCount(row?.prompt_tokens) + tokenCount(row?.completion_tokens)
const dateKey = date => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
const parseLocalDay = key => new Date(`${key}T00:00:00`)

function Usage({ settings, refreshNonce }) {
  const [state, setState] = useState({ daily: [], models: [], total: {}, generated_at: 0 })
  const [period, setPeriod] = useState('daily')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hoverDay, setHoverDay] = useState('')
  async function load() {
    setLoading(true); setError('')
    try { setState(await request('unified/usage')) }
    catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [settings?.retention_days, refreshNonce])

  const dailyMap = useMemo(() => new Map((state.daily || []).map(row => [row.day, row])), [state.daily])
  const retentionDays = Math.min(365, Math.max(30, Number(settings?.retention_days || state.retention_days || 365)))
  const heatmap = useMemo(() => {
    const today = new Date(); today.setHours(0, 0, 0, 0)
    const cutoff = new Date(today); cutoff.setDate(cutoff.getDate() - retentionDays + 1)
    const first = new Date(cutoff); first.setDate(first.getDate() - ((first.getDay() + 6) % 7))
    const weeks = []
    for (let cursor = new Date(first); cursor <= today; cursor.setDate(cursor.getDate() + 7)) {
      const week = []
      for (let weekday = 0; weekday < 7; weekday += 1) {
        const day = new Date(cursor); day.setDate(cursor.getDate() + weekday)
        const key = dateKey(day)
        week.push({ key, visible: day >= cutoff && day <= today, row: dailyMap.get(key), day })
      }
      weeks.push(week)
    }
    const max = Math.max(0, ...state.daily.map(totalTokens))
    const monthLabels = weeks.map((week, index) => {
      const firstVisible = week.find(cell => cell.visible)
      return firstVisible && (index === 0 || firstVisible.day.getDate() <= 7)
        ? { index, label: `${firstVisible.day.getMonth() + 1}月` } : null
    }).filter(Boolean)
    return { weeks, max, monthLabels }
  }, [dailyMap, retentionDays, state.daily])

  const todayKey = dateKey(new Date())
  const firstRecordedAt = state.total?.first_recorded_at
  const summary = useMemo(() => {
    const sumSince = days => {
      const from = new Date(); from.setHours(0, 0, 0, 0); from.setDate(from.getDate() - days + 1)
      return state.daily.filter(row => row.day >= dateKey(from)).reduce((sum, row) => sum + totalTokens(row), 0)
    }
    return { today: totalTokens(dailyMap.get(todayKey)), week: sumSince(7), month: sumSince(30), total: totalTokens(state.total) }
  }, [dailyMap, state.daily, state.total, todayKey])

  const trend = useMemo(() => {
    const byDay = new Map((state.daily || []).map(row => [row.day, row]))
    if (period === 'daily') {
      const end = new Date(); end.setHours(0, 0, 0, 0)
      return Array.from({ length: Math.min(30, retentionDays) }, (_, index) => {
        const day = new Date(end); day.setDate(end.getDate() - (Math.min(30, retentionDays) - index - 1))
        const key = dateKey(day); const row = byDay.get(key) || {}
        return { key, label: `${day.getMonth() + 1}/${day.getDate()}`, tokens: totalTokens(row) }
      })
    }
    const grouped = new Map()
    for (const row of state.daily || []) {
      const day = parseLocalDay(row.day)
      if (period === 'weekly') {
        day.setDate(day.getDate() - ((day.getDay() + 6) % 7))
      } else day.setDate(1)
      const key = dateKey(day)
      const item = grouped.get(key) || { key, label: `${day.getMonth() + 1}${period === 'weekly' ? `/${day.getDate()}` : '月'}`, tokens: 0 }
      item.tokens += totalTokens(row); grouped.set(key, item)
    }
    if (period === 'total') {
      const now = new Date(); const monthCount = Math.min(12, Math.ceil(retentionDays / 30.4) + 1)
      const months = Array.from({ length: monthCount }, (_, index) => {
        const month = new Date(now.getFullYear(), now.getMonth() - (monthCount - index - 1), 1)
        const key = dateKey(month)
        return grouped.get(key) || { key, label: `${month.getMonth() + 1}月`, tokens: 0 }
      })
      let cumulative = 0
      return months.map(item => ({ ...item, tokens: cumulative += item.tokens }))
    }
    const count = Math.min(12, Math.ceil(retentionDays / 7))
    const currentMonday = new Date(); currentMonday.setHours(0, 0, 0, 0); currentMonday.setDate(currentMonday.getDate() - ((currentMonday.getDay() + 6) % 7))
    return Array.from({ length: count }, (_, index) => {
      const week = new Date(currentMonday); week.setDate(currentMonday.getDate() - (count - index - 1) * 7)
      const key = dateKey(week)
      return grouped.get(key) || { key, label: `${week.getMonth() + 1}/${week.getDate()}`, tokens: 0 }
    })
  }, [period, retentionDays, state.daily])

  const chartModels = useMemo(() => {
    const palette = ['#368dcc', '#37a978', '#8666d9', '#ed6668', '#d4a33b', '#34a5ad']
    const ranked = (state.models || []).map(row => ({ ...row, tokens: totalTokens(row) })).sort((a, b) => b.tokens - a.tokens)
    const visible = ranked.slice(0, 5).map((row, index) => ({ ...row, color: palette[index] }))
    const rest = ranked.slice(5)
    if (rest.length) visible.push({ model: '其他模型', tokens: rest.reduce((sum, row) => sum + row.tokens, 0), requests: rest.reduce((sum, row) => sum + tokenCount(row.requests), 0), color: palette[5] })
    return visible
  }, [state.models])
  const modelTotal = chartModels.reduce((sum, item) => sum + item.tokens, 0)
  const circumference = 2 * Math.PI * 56
  let accumulated = 0
  const ring = chartModels.map(item => {
    const length = modelTotal ? item.tokens / modelTotal * circumference : 0
    const segment = { ...item, length, offset: accumulated }
    accumulated += length
    return segment
  })
  const maxTrend = Math.max(1, ...trend.map(item => item.tokens))

  return <div className="stack usage-page">
    <section className="summary-grid usage-summary">
      {[["今日 Token", summary.today], ["近 7 日", summary.week], ["近 30 日", summary.month], ["累计 Token", summary.total]].map(([label, value]) => <article className="summary-card" key={label}><div><strong title={`${exactCount(value)} Token`}>{tokenText(value)}</strong><span>{label}</span></div></article>)}
    </section>
    <section className="content-card usage-heatmap-card">
      <div className="section-head"><div><span className="section-label">活动日历</span><h2>Token 消耗热力图</h2><p className="muted">每格代表一天；颜色越深，当天消耗越多。当前保留 {retentionDays} 天。</p></div><button className="button secondary" onClick={load} disabled={loading}><RefreshCw size={14} className={loading ? 'spin' : ''}/>刷新</button></div>
      {error && <div className="result-banner error">统计加载失败：{error}</div>}
      <div className="heatmap-scroll" aria-label="每日 Token 消耗日历">
        <div className="heatmap-days"><span>一</span><span></span><span>三</span><span></span><span>五</span><span></span><span></span></div>
        <div className="heatmap-body">
          <div className="heatmap-months" style={{ gridTemplateColumns: `repeat(${heatmap.weeks.length}, 12px)` }}>{heatmap.monthLabels.map(item => <span key={item.index} style={{ gridColumn: item.index + 1 }}>{item.label}</span>)}</div>
          <div className="heatmap-weeks" style={{ gridTemplateColumns: `repeat(${heatmap.weeks.length}, 12px)` }}>
            {heatmap.weeks.map((week, index) => <div className="heatmap-week" key={index}>{week.map(cell => {
              const count = totalTokens(cell.row)
              const level = count ? Math.min(4, Math.max(1, Math.ceil(count / Math.max(1, heatmap.max) * 4))) : 0
              const description = `${cell.key} · ${tokenText(count)} Token · ${cell.row?.requests || 0} 次请求`
              return <span key={cell.key} className={`heat-cell level-${level} ${cell.visible ? '' : 'outside'}`} title={cell.visible ? `${cell.key} · ${exactCount(count)} Token · ${cell.row?.requests || 0} 次请求` : ''} aria-label={cell.visible ? description : undefined} onMouseEnter={() => cell.visible && setHoverDay(description)} onMouseLeave={() => setHoverDay('')}/>
            })}</div>)}
          </div>
        </div>
      </div>
      <div className="heatmap-footer"><span className="muted">{hoverDay || '将鼠标移到方格查看当天详情'}</span><span className="heat-legend"><small>少</small>{[0,1,2,3,4].map(level => <i key={level} className={`heat-cell level-${level}`}/>)}<small>多</small></span></div>
      {firstRecordedAt && <p className="muted usage-footnote">从 {new Date(firstRecordedAt * 1000).toLocaleString('zh-CN')} 开始记录；此前的调用没有历史 Token 记录，无法回填到统计页。</p>}
    </section>
    <div className="usage-lower-grid">
      <section className="content-card trend-card">
        <div className="section-head"><div><span className="section-label">消耗趋势</span><h2>{period === 'daily' ? '每日 Token' : period === 'weekly' ? '每周 Token' : '按月累计 Token'}</h2></div><div className="filter-tabs" role="tablist" aria-label="统计周期">{[['daily','每日'],['weekly','每周'],['total','累计']].map(([id,label]) => <button key={id} role="tab" aria-selected={period === id} className={period === id ? 'active' : ''} onClick={() => setPeriod(id)}>{label}</button>)}</div></div>
        <div className="trend-chart" role="img" aria-label="Token 消耗趋势柱状图">
          {trend.length ? trend.map((item, index) => <div className="trend-column" key={item.key} title={`${item.label} · ${exactCount(item.tokens)} Token`}><span className="trend-value">{tokenText(item.tokens)}</span><i style={{ height: `${Math.max(item.tokens ? 3 : 0, item.tokens / maxTrend * 100)}%` }}/><small>{index === 0 || index === trend.length - 1 || index % Math.max(1, Math.ceil(trend.length / 8)) === 0 ? item.label : ''}</small></div>) : <Empty>暂无统计数据，产生调用后会显示 Token 用量</Empty>}
        </div>
        <p className="muted usage-footnote">用量取自上游返回的 Token 统计；若上游未返回用量字段，该次请求会显示为 0。</p>
      </section>
      <section className="content-card model-usage-card">
        <div className="section-head"><div><span className="section-label">模型分布</span><h2>按模型统计</h2></div></div>
        {modelTotal ? <div className="model-usage-content">
          <div className="donut-wrap"><svg viewBox="0 0 144 144" role="img" aria-label={`累计 ${exactCount(modelTotal)} Token`}><circle className="donut-track" cx="72" cy="72" r="56"/><g transform="rotate(-90 72 72)">{ring.map(item => <circle key={item.model} cx="72" cy="72" r="56" fill="none" stroke={item.color} strokeWidth="22" strokeDasharray={`${item.length} ${circumference - item.length}`} strokeDashoffset={-item.offset}/>)}</g></svg><div><strong title={`${exactCount(modelTotal)} Token`}>{tokenText(modelTotal)}</strong><span>Token</span></div></div>
          <div className="model-usage-list">{chartModels.map(item => <div className="model-usage-row" key={item.model}><div><i style={{ background: item.color }}/><code title={item.model}>{item.model}</code><strong>{modelTotal ? `${(item.tokens / modelTotal * 100).toFixed(1)}%` : '0%'}</strong></div><small title={`${exactCount(item.tokens)} Token`}>{tokenText(item.tokens)} Token · {exactCount(item.requests)} 次请求</small></div>)}</div>
        </div> : <Empty>{loading ? '正在读取用量…' : '暂无 Token 统计数据'}</Empty>}
      </section>
    </div>
  </div>
}

function SettingsPage({ settings, theme, onThemeChange, onSave, onNavigate }) {
  const [form, setForm] = useState(settings)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => setForm(settings), [settings])
  async function save(event) {
    event.preventDefault(); setBusy(true); setMessage('')
    if (Number(form.retention_days) < Number(settings.retention_days) && !window.confirm(`缩短保留时间会立即删除早于 ${form.retention_days} 天的统计记录，是否继续？`)) { setBusy(false); return }
    try { await onSave(form); setMessage('设置已保存') }
    catch (err) { setMessage(err.message) }
    finally { setBusy(false) }
  }
  return <div className="settings-grid console-settings">
    <section className="content-card"><span className="section-label">外观</span><h2>控制台主题</h2><p className="muted settings-copy">主题只保存在当前浏览器中，不影响其他用户。</p><div className="theme-options">
      {[['light',Sun,'浅色'],['dark',Moon,'深色']].map(([id,Icon,label]) => <button type="button" key={id} className={`theme-option ${theme === id ? 'active' : ''}`} onClick={() => onThemeChange(id)}><Icon size={18}/><span>{label}</span>{theme === id && <Check size={15}/>}</button>)}
    </div></section>
    <form className="content-card" onSubmit={save}><span className="section-label">数据与体验</span><h2>统计与刷新</h2>
      <label htmlFor="retention-days">调用统计保留时间</label><select id="retention-days" value={form.retention_days || 365} onChange={e => setForm(current => ({ ...current, retention_days: Number(e.target.value) }))}>{[30,90,180,365].map(days => <option key={days} value={days}>{days} 天</option>)}</select><p className="settings-copy muted">只保留请求时间、模型、状态、耗时和 Token 数；不会保存提示词或回复正文。当前数据最多可保留 365 天。</p>
      <label htmlFor="auto-refresh">自动刷新间隔</label><select id="auto-refresh" value={form.auto_refresh_seconds || 0} onChange={e => setForm(current => ({ ...current, auto_refresh_seconds: Number(e.target.value) }))}><option value="0">关闭</option><option value="30">30 秒</option><option value="60">1 分钟</option><option value="300">5 分钟</option></select><p className="settings-copy muted">开启后，控制台会按间隔刷新账号状态和统计数据。</p>
      <button className="button primary wide" disabled={busy}><Save size={15}/>{busy ? '正在保存…' : '保存设置'}</button>{message && <div className={`result-banner settings-message ${message !== '设置已保存' ? 'error' : ''}`}>{message}</div>}
    </form>
    <section className="content-card settings-wide"><span className="section-label">数据说明</span><h2>Token 统计如何计算</h2><p className="muted settings-copy">统计从每次 API 返回的 usage 字段读取输入和输出 Token。更换保留时间后，超出范围的历史统计会清理；已清理的数据无法恢复。累计数字表示当前保留范围内的用量。</p><button className="button secondary" type="button" onClick={() => onNavigate('logs')}><FileText size={14}/>查看调用记录</button></section>
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
  const models = (data?.models || []).map(item => item.id)
  const [model, setModel] = useState('')
  const [message, setMessage] = useState('请只回复：连接成功')
  const [output, setOutput] = useState('')
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    if (!models.includes(model)) { setFailed(true); setOutput('请从建议列表中选择有效模型'); return }
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
      <input id="test-model" list="test-model-options" value={model} onChange={e => setModel(e.target.value)} placeholder="输入模型名称搜索" autoComplete="off" required/>
      <datalist id="test-model-options">{models.map(id => <option key={id} value={id}/>)}</datalist>
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
  const [editingConnection, setEditingConnection] = useState(null)
  const [editingRoute, setEditingRoute] = useState(null)
  const [consoleSettings, setConsoleSettings] = useState({ retention_days: 365, auto_refresh_seconds: 0 })
  const [theme, setTheme] = useState(() => window.localStorage.getItem('unified-theme') || 'light')
  const [refreshNonce, setRefreshNonce] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [overview, session, preferences] = await Promise.all([request('unified/overview'), request('session'), request('unified/settings')])
      setData(overview); setCsrf(session.csrf); setConsoleSettings(preferences.settings); setAuthenticated(true); setRefreshNonce(value => value + 1)
    }
    catch (err) { if (err.status === 401) setAuthenticated(false) }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])
  useEffect(() => {
    document.documentElement.dataset.theme = theme
    window.localStorage.setItem('unified-theme', theme)
  }, [theme])
  useEffect(() => {
    const interval = Number(consoleSettings.auto_refresh_seconds || 0)
    if (!authenticated || !interval) return undefined
    const timer = window.setInterval(() => { void load() }, interval * 1000)
    return () => window.clearInterval(timer)
  }, [authenticated, consoleSettings.auto_refresh_seconds, load])
  async function login(key) { const result = await request('login', { method: 'POST', body: { key } }); setCsrf(result.csrf); setAuthenticated(true); await load() }
  async function logout() { await request('logout', { method: 'POST', body: {}, csrf }); setData(null); setAuthenticated(false) }
  function navigate(next) { setPage(next); setDrawer(false) }
  async function saved(keepOpen = false) { await load(); if (!keepOpen) setDialog(null) }
  async function saveConsoleSettings(form) {
    const result = await request('unified/settings', { method: 'PATCH', body: form, csrf })
    setConsoleSettings(result.settings)
  }

  if (loading && !data) return <div className="boot"><RefreshCw className="spin" size={20}/><span>正在连接控制台</span></div>
  if (!authenticated) return <Login onLogin={login}/>
  const [title, description] = pageMeta[page]
  const pages = {
    overview: <Overview data={data} onNavigate={navigate}/>,
    accounts: <Accounts data={data} csrf={csrf} onRefresh={load} onAdd={() => setDialog('account')}/>,
    connections: <Connections data={data} csrf={csrf} onRefresh={load} onAdd={() => { setEditingConnection(null); setDialog('connection') }} onEdit={connection => { setEditingConnection(connection); setDialog('connection') }}/>,
    models: <Models data={data} onNavigate={navigate}/>,
    keys: <Keys data={data} csrf={csrf} onRefresh={load} onAdd={() => setDialog('key')}/>,
    test: <TestPanel data={data} csrf={csrf}/>,
    routes: <Routes data={data} csrf={csrf} onRefresh={load} onAdd={() => { setEditingRoute(null); setDialog('route') }} onEdit={route => { setEditingRoute(route); setDialog('route') }}/>,
    logs: <Logs csrf={csrf}/>,
    automation: <Automation data={data} csrf={csrf} onRefresh={load}/>,
    backup: <Backup csrf={csrf}/>,
    usage: <Usage settings={consoleSettings} refreshNonce={refreshNonce}/>,
    settings: <SettingsPage settings={consoleSettings} theme={theme} onThemeChange={setTheme} onSave={saveConsoleSettings} onNavigate={navigate}/>,
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
      <div className="sidebar-bottom"><button className={page === 'settings' ? 'active' : ''} onClick={() => navigate('settings')}><Settings2 size={17}/>设置</button><button onClick={logout}><LogOut size={17}/>退出登录</button></div>
    </aside>
    <div className="workspace">
      <header className="topbar"><button className="menu-button" onClick={() => setDrawer(true)}><Menu size={19}/></button><div><span>Unified / {title}</span></div><div className="top-actions"><span className="live-status"><i/>运行中</span><button className="icon-action" onClick={load} aria-label="刷新"><RefreshCw size={16} className={loading ? 'spin' : ''}/></button></div></header>
      <main className="page"><header className="page-heading"><div><h1>{title}</h1><p>{description}</p></div>{page === 'accounts' && <button className="button primary" onClick={() => setDialog('account')}><Plus size={15}/>添加账号</button>}</header>{content}</main>
    </div>
    {dialog === 'account' && (
      <AccountDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
    {dialog === 'connection' && (
      <ConnectionDialog key={editingConnection?.id || 'new'} csrf={csrf} initialConnection={editingConnection} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
    {dialog === 'key' && (
      <KeyDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved(true)}/>
    )}
    {dialog === 'route' && (
      <RouteDialog key={editingRoute?.id || 'new'} data={data} csrf={csrf} initialRoute={editingRoute} onClose={() => setDialog(null)} onSaved={() => saved()}/>
    )}
  </div>
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
