import React, { useEffect, useMemo, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  Activity, Blocks, Bot, ChevronDown, CircleGauge, Database,
  FlaskConical, KeyRound, LogOut, Menu, Plus, RefreshCw,
  Search, Server, Settings2, Users, X,
} from 'lucide-react'
import './styles.css'

const pageMeta = {
  overview: ['概览', '服务状态和常用入口'],
  accounts: ['账号', '统一管理所有平台账号'],
  connections: ['自定义服务', '接入 OpenAI 兼容接口'],
  models: ['模型', '查看当前可以调用的模型'],
  keys: ['API 密钥', '管理客户端访问凭据'],
  test: ['调用测试', '快速验证模型连接'],
}

const navGroups = [
  { label: '工作台', items: [['overview', CircleGauge], ['accounts', Users], ['connections', Server], ['models', Bot]] },
  { label: '开发', items: [['keys', KeyRound], ['test', FlaskConical]] },
]

const providerName = id => ({ trae: 'TRAE', codebuddy: 'CodeBuddy', monkeycode: 'MonkeyCode' }[id] || id)
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

function Accounts({ data }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const rows = useMemo(() => (data?.accounts || []).filter(a => (provider === 'all' || a.provider === provider) && `${a.name || ''} ${a.nickname || ''} ${a.uid || ''}`.toLowerCase().includes(query.toLowerCase())), [data, provider, query])
  return <section className="content-card">
    <div className="section-head"><div><span className="section-label">账号池</span><h2>{data?.accounts?.length || 0} 个账号</h2></div><div className="head-actions"><button className="button secondary">更新额度</button><button className="button primary"><Plus size={15}/>添加账号</button></div></div>
    <div className="toolbar"><div className="filter-tabs">{[['all','全部'],['trae','TRAE'],['codebuddy','CodeBuddy'],['monkeycode','MonkeyCode']].map(([id,label]) => <button key={id} className={provider === id ? 'active' : ''} onClick={() => setProvider(id)}>{label}</button>)}</div><label className="search-box"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索账号" /></label></div>
    <DataTable columns={['账号','平台','状态','额度','有效期','']} empty={!rows.length && '没有匹配的账号'}>{rows.map(a => { const status = a.enabled === false ? 'paused' : a.pool_state || a.status || 'ready'; return <tr key={`${a.provider}-${a.id}`}><td><strong>{a.name || a.nickname || a.uid}</strong><small>{a.uid || a.id}</small></td><td><span className="provider-badge">{providerName(a.provider)}</span></td><td><StatusBadge status={status}/></td><td className="tabular">{typeof a.remaining === 'number' ? a.remaining.toLocaleString() : '—'}</td><td><span className="muted">{a.expires_at ? new Date(a.expires_at).toLocaleDateString('zh-CN') : '未提供'}</span></td><td className="row-menu"><button aria-label="账号操作">•••</button></td></tr> })}</DataTable>
  </section>
}

function Connections({ data }) {
  const rows = data?.connections || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">自定义服务</span><h2>兼容 OpenAI 的接口</h2><p className="muted">使用 Base URL 与 API Key 接入其他服务。</p></div><button className="button primary"><Plus size={15}/>添加服务</button></div><DataTable columns={['服务','模型前缀','状态','模型数','']} empty={!rows.length && '暂未添加自定义服务'}>{rows.map(item => <tr key={item.id}><td><strong>{item.name}</strong><small>{item.base_url}</small></td><td><code>{item.id}/</code></td><td><StatusBadge status={item.enabled ? 'ready' : 'paused'}/></td><td className="tabular">{item.models?.length || 0}</td><td className="row-menu"><button aria-label="服务操作">•••</button></td></tr>)}</DataTable></section>
}

function Models({ data, onNavigate }) {
  const [query, setQuery] = useState('')
  const rows = (data?.models || []).filter(m => m.id.toLowerCase().includes(query.toLowerCase()))
  return <section className="content-card"><div className="section-head"><div><span className="section-label">模型目录</span><h2>{data?.models?.length || 0} 个可用模型</h2></div><label className="search-box"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型" /></label></div><DataTable columns={['模型 ID','来源','兼容接口','']} empty={!rows.length && '没有匹配的模型'}>{rows.map(m => <tr key={m.id}><td><code>{m.id}</code></td><td><span className="provider-badge">{providerName(m.owned_by)}</span></td><td className="muted">{m.owned_by === 'codebuddy' ? 'Chat · Responses · Messages' : 'Chat Completions'}</td><td><button className="link-button" onClick={() => onNavigate('test')}>测试</button></td></tr>)}</DataTable></section>
}

function Keys({ data }) {
  const rows = data?.keys || []
  return <section className="content-card"><div className="section-head"><div><span className="section-label">访问控制</span><h2>API 密钥</h2></div><button className="button primary"><Plus size={15}/>创建密钥</button></div><DataTable columns={['名称','前缀','创建时间','']} empty={!rows.length && '暂未创建 API 密钥'}>{rows.map(k => <tr key={k.id}><td><strong>{k.name}</strong></td><td><code>{k.prefix || '已隐藏'}…</code></td><td className="muted">{k.created ? new Date(k.created * 1000).toLocaleDateString('zh-CN') : '—'}</td><td className="row-menu"><button>•••</button></td></tr>)}</DataTable></section>
}

function TestPanel({ data }) {
  return <div className="test-layout"><section className="content-card"><span className="section-label">请求</span><h2>调用测试</h2><label>模型</label><select defaultValue=""><option value="" disabled>选择模型</option>{(data?.models || []).map(m => <option key={m.id}>{m.id}</option>)}</select><label>消息</label><textarea rows="7" placeholder="输入一条测试消息"/><button className="button primary wide">发送请求</button></section><section className="content-card response-panel"><span className="section-label">响应</span><h2>模型回复</h2><Empty>发送请求后，结果会显示在这里</Empty></section></div>
}

function App() {
  const [csrf, setCsrf] = useState('')
  const [data, setData] = useState(null)
  const [page, setPage] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [authenticated, setAuthenticated] = useState(true)
  const [drawer, setDrawer] = useState(false)

  async function load() {
    setLoading(true)
    try { setData(await request('unified/overview')); setAuthenticated(true) }
    catch (err) { if (err.status === 401) setAuthenticated(false) }
    finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])
  async function login(key) { const result = await request('login', { method: 'POST', body: { key } }); setCsrf(result.csrf); setAuthenticated(true); await load() }
  async function logout() { await request('logout', { method: 'POST', body: {}, csrf }); setData(null); setAuthenticated(false) }
  function navigate(next) { setPage(next); setDrawer(false) }

  if (loading && !data) return <div className="boot"><RefreshCw className="spin" size={20}/><span>正在连接控制台</span></div>
  if (!authenticated) return <Login onLogin={login}/>
  const [title, description] = pageMeta[page]
  const content = page === 'overview' ? <Overview data={data} onNavigate={navigate}/> : page === 'accounts' ? <Accounts data={data}/> : page === 'connections' ? <Connections data={data}/> : page === 'models' ? <Models data={data} onNavigate={navigate}/> : page === 'keys' ? <Keys data={data}/> : <TestPanel data={data}/>

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
      <main className="page"><header className="page-heading"><div><h1>{title}</h1><p>{description}</p></div>{page === 'accounts' && <button className="button primary"><Plus size={15}/>添加账号</button>}</header>{content}</main>
    </div>
  </div>
}

createRoot(document.getElementById('root')).render(<React.StrictMode><App /></React.StrictMode>)
