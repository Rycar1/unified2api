import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { createRoot } from 'react-dom/client'
import {
  Activity,
  Bot,
  Check,
  ChevronDown,
  CircleGauge,
  Copy,
  Database,
  ExternalLink,
  Moon,
  Sun,
  Archive,
  Clock,
  Download,
  FileText,
  FlaskConical,
  KeyRound,
  LogOut,
  Menu,
  Monitor,
  Network,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Save,
  Search,
  Server,
  Settings2,
  Trash2,
  Upload,
  Users,
  WalletCards,
  X,
} from 'lucide-react'
import './styles.css'
import {
  CapabilityBadges,
  KeyLimitsDialog,
  RequestDetailsDialog,
  ModelCapabilitiesDialog,
  ReleasesPage,
} from './controls'
import { MotionRows, PagePresence, useDialogFocus, useMedia, DrawerFocus } from './motion'
import { TasksPage, QuotaDialog, ProviderSettings, PerformancePage, BulkImportDialog, ClientsPage } from './extensions'
import './refresh.css'
import { useTheme, ThemeToggle } from './theme'
import './theme.css'
import { Dashboard } from './dashboard'
import { uniqueModels } from './model-list'
import { AccountProbeDialog } from './account-probe'
import { NetworkPanel, RoutePlanDialog, ConnectionPoolDialog } from './operations'
import { ProxySettingsPanel } from './proxy-settings'
import { AutomationCenter } from './automation-plans'
import { AntigravityCallback } from './antigravity'
import { ModelAliasesPanel } from './reliability'
import { ProbePlansPage } from './probe-plans'
import { ConfigHistoryPage } from './config-history'
import { CreditPolicyPanel } from './credit-policy'

const pageMeta = {
  tasks: ['福利任务', '领取账号福利，查看任务进度和额度发放'],
  performance: ['性能与费用', '缓存命中、生成速度和等价费用'],
  clients: ['客户端接入', '生成本机配置包，保留原配置备份'],
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
  releases: ['版本管理', '检查更新、升级和回滚'],
  probes: ['兼容性巡检', '独立测试模型接口、工具调用与流式结束'],
  history: ['配置历史', '查看修改差异，恢复单项配置'],
  settings: ['设置', '调整外观、统计保留时间和自动刷新'],
}

const navGroups = [
  {
    label: '工作台',
    items: [
      ['overview', CircleGauge],
      ['accounts', Users],
      ['connections', Server],
      ['models', Bot],
    ],
  },
  {
    label: '开发',
    items: [
      ['keys', KeyRound],
      ['test', FlaskConical],
      ['routes', Network],
      ['clients', Download],
    ],
  },
  {
    label: '分析与福利',
    items: [
      ['usage', Activity],
      ['performance', CircleGauge],
      ['tasks', WalletCards],
    ],
  },
  {
    label: '运维',
    items: [
      ['logs', FileText],
      ['automation', Clock],
      ['probes', FlaskConical],
      ['history', FileText],
      ['backup', Archive],
      ['releases', Download],
    ],
  },
]

const accountProviders = [
  ['trae', 'TRAE'],
  ['codebuddy', 'CodeBuddy 国内'],
  ['workbuddy_intl', 'WorkBuddy AI 国外'],
  ['monkeycode', 'MonkeyCode'],
  ['qoder', 'Qoder'],
  ['antigravity', 'Antigravity'],
]
const providerName = (id) =>
  ({
    ...Object.fromEntries(accountProviders),
    qoder: 'Qoder 国际',
    qoder_cn: 'Qoder 国内',
    workbuddy: 'WorkBuddy AI 国外',
    route: '智能路由',
    alias: '模型别名',
  })[id] || id
const accountFilter = (a) => (a.provider === 'qoder' ? (a.region === 'cn' ? 'qoder_cn' : 'qoder_global') : a.provider)
const accountPlatform = (a) =>
  a.provider === 'qoder' ? (a.region === 'cn' ? 'Qoder 国内' : 'Qoder 国际') : providerName(a.provider)
const accountFilters = [
  ...accountProviders.filter(([id]) => id !== 'qoder'),
  ['qoder_cn', 'Qoder 国内'],
  ['qoder_global', 'Qoder 国际'],
]
const statusName = (value) =>
  ({
    ready: '可用',
    available: '可用',
    paused: '已暂停',
    cooling: '冷却中',
    invalid: '凭据异常',
    exhausted: '额度耗尽',
    expired: '已过期',
    error: '异常',
  })[value] ||
  value ||
  '可用'

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
    const detail = data.error || data.detail
    const message = typeof detail === 'string' ? detail : detail?.message || '请求失败，请稍后重试'
    const suggestions = detail?.suggestions?.length ? `；建议模型：${detail.suggestions.join('、')}` : ''
    const error = new Error(message + suggestions)
    error.status = response.status
    throw error
  }
  return data
}

function Login({ onLogin, themeControl }) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await onLogin(key)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="login-shell">
      <section className="login-panel glass-panel">
        <div className="login-brand">
          <div className="wordmark">Unified</div>
          {themeControl}
        </div>
        <p className="kicker">本地模型网关</p>
        <h1>登录控制台</h1>
        <p className="muted">管理账号、模型和统一 API 接入。</p>
        <form onSubmit={submit}>
          <label htmlFor="admin-key">管理密钥</label>
          <input
            id="admin-key"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="输入 ADMIN_KEY"
            autoComplete="current-password"
            required
          />
          {error && <p className="form-error">{error}</p>}
          <button className="button primary wide" disabled={busy}>
            {busy ? '正在登录…' : '进入控制台'}
          </button>
        </form>
      </section>
    </main>
  )
}

function StatusBadge({ status }) {
  const tone = ['ready', 'available'].includes(status)
    ? 'success'
    : ['paused', 'cooling'].includes(status)
      ? 'warning'
      : 'danger'
  return (
    <span className={`status-badge ${tone}`}>
      <i />
      {statusName(status)}
    </span>
  )
}

function Empty({ children }) {
  return (
    <div className="empty">
      <Database size={22} />
      <span>{children}</span>
    </div>
  )
}

function DataTable({ children, empty, columns }) {
  return (
    <div className="table-shell">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c}>{c}</th>
            ))}
          </tr>
        </thead>
        <MotionRows>{children}</MotionRows>
      </table>
      {empty && <Empty>{empty}</Empty>}
    </div>
  )
}

function Modal({ title, subtitle, onClose, children }) {
  const element = useRef(null)
  useDialogFocus(element, onClose)
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      onMouseDown={(event) => event.target === event.currentTarget && onClose()}
    >
      <section ref={element} className="modal-card" role="dialog" aria-modal="true" aria-label={title}>
        <header className="modal-head">
          <div>
            <span className="section-label">{subtitle}</span>
            <h2>{title}</h2>
          </div>
          <button className="icon-action" onClick={onClose} aria-label="关闭">
            <X size={17} />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

function AccountDialog({ csrf, onClose, onSaved }) {
  const [provider, setProvider] = useState('trae')
  const [mode, setMode] = useState('web')
  const [name, setName] = useState('')
  const [qoderRegion, setQoderRegion] = useState('global')
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
        if (flow.provider === 'codebuddy')
          result = await request(`oauth/${flow.id}/poll`, { method: 'POST', body: {}, csrf })
        if (flow.provider === 'monkeycode') result = await request(`unified/monkeycode/login/${flow.id}`)
        if (flow.provider === 'workbuddy_intl') result = await request(`unified/workbuddy/login/${flow.id}`)
        if (flow.provider === 'qoder') result = await request(`unified/qoder/login/${flow.id}`)
        if (flow.provider === 'antigravity') result = await request(`unified/antigravity/login/${flow.id}`)
        if (stopped) return
        const state = result.state || result.status
        if (state === 'success') {
          setStatus('登录成功，账号已经保存')
          await onSaved()
          return
        }
        if (['failed', 'cancelled', 'expired'].includes(state)) {
          setFlow(null)
          setError(result.message || '登录已结束，请重新开始')
          return
        }
        setStatus(state === 'validating' ? '正在校验并保存账号…' : '请在打开的官方页面完成登录…')
        timer = setTimeout(poll, 2500)
      } catch (err) {
        if (!stopped) {
          setFlow(null)
          setError(err.message)
        }
      }
    }
    timer = setTimeout(poll, 1200)
    return () => {
      stopped = true
      clearTimeout(timer)
    }
  }, [flow, csrf, onSaved])

  async function cancelFlow() {
    if (!flow) return
    const path =
      flow.provider === 'codebuddy'
        ? `oauth/${flow.id}`
        : `unified/${flow.provider === 'workbuddy_intl' ? 'workbuddy' : flow.provider}/login/${flow.id}`
    try {
      await request(path, { method: 'DELETE', body: {}, csrf })
    } catch {
      /* completed or expired */
    }
    setFlow(null)
  }
  async function close() {
    await cancelFlow()
    onClose()
  }
  async function startLogin() {
    const popup = provider === 'monkeycode' ? null : window.open('about:blank', '_blank')
    if (popup) popup.opener = null
    setBusy(true)
    setError('')
    setStatus('正在生成登录链接…')
    try {
      let result
      if (provider === 'trae') result = await request('unified/trae/login', { method: 'POST', body: {}, csrf })
      if (provider === 'codebuddy')
        result = await request('oauth/start', { method: 'POST', body: { name: name.trim() || undefined }, csrf })
      if (provider === 'monkeycode')
        result = await request('unified/monkeycode/login', { method: 'POST', body: { name: name.trim() }, csrf })
      if (provider === 'workbuddy_intl')
        result = await request('unified/workbuddy/login', { method: 'POST', body: { name: name.trim() }, csrf })
      if (provider === 'qoder')
        result = await request('unified/qoder/login', {
          method: 'POST',
          body: { name: name.trim(), region: qoderRegion },
          csrf,
        })
      if (provider === 'antigravity')
        result = await request('unified/antigravity/login', { method: 'POST', body: { name: name.trim() }, csrf })
      const next = {
        provider,
        id: result.pending_id || result.id,
        url: result.login_url || result.url || result.launch_url,
        manual: result.manual_callback,
      }
      setFlow(next)
      setStatus('登录页面已打开，请完成官方登录')
      if (provider === 'monkeycode') {
        const link = document.createElement('a')
        link.href = next.url
        link.click()
      } else if (next.url && popup) popup.location.replace(next.url)
      else popup?.close()
    } catch (err) {
      popup?.close()
      setError(err.message)
      setStatus('')
    } finally {
      setBusy(false)
    }
  }
  async function importAccount(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      let body = { name: name.trim() }
      if (provider === 'qoder') body.region = qoderRegion
      if (provider === 'monkeycode') {
        const value = credential.trim()
        if (value.startsWith('{')) body.credential = JSON.parse(value)
        else body.cookie = value
      } else if (provider === 'antigravity' || provider === 'qoder' && credential.trim().startsWith('pt-')) {
        body.credential = credential.trim()
      } else {
        body.credential = JSON.parse(credential)
      }
      const path = provider === 'codebuddy' ? 'accounts' : `unified/${provider}/accounts`
      await request(path, { method: 'POST', body, csrf })
      await onSaved()
    } catch (err) {
      setError(err instanceof SyntaxError ? '请输入有效的 JSON 凭据' : err.message)
    } finally {
      setBusy(false)
    }
  }
  async function loadFile(event) {
    const file = event.target.files?.[0]
    if (!file) return
    if (file.size > 1024 * 1024) {
      setError('凭据文件不能超过 1 MB')
      return
    }
    setCredential((await file.text()).replace(/^\uFEFF/, ''))
  }
  function changeProvider(next) {
    cancelFlow()
    setProvider(next)
    setMode('web')
    setStatus('')
    setError('')
    setCredential('')
  }

  return (
    <Modal title="添加账号" subtitle="连接平台" onClose={close}>
      <div className="choice-grid">
        {accountProviders.map(([id, label]) => (
          <button key={id} className={provider === id ? 'active' : ''} onClick={() => changeProvider(id)}>
            {label}
            {provider === id && <Check size={14} />}
          </button>
        ))}
      </div>
      {provider === 'qoder' && (
        <>
          <label htmlFor="qoder-region">Qoder 站点</label>
          <select
            id="qoder-region"
            value={qoderRegion}
            onChange={(e) => setQoderRegion(e.target.value)}
            disabled={Boolean(flow)}
          >
            <option value="global">国际版</option>
            <option value="cn">国内版</option>
          </select>
        </>
      )}
      {provider !== 'workbuddy_intl' && (
        <>
          <label>账号备注（可选）</label>
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength="60" placeholder="例如：日常账号" />
        </>
      )}
      {provider !== 'workbuddy_intl' && (
        <div className="mode-tabs">
          <button className={mode === 'web' ? 'active' : ''} onClick={() => setMode('web')}>
            网页登录
          </button>
          <button className={mode === 'import' ? 'active' : ''} onClick={() => setMode('import')}>
            导入凭据
          </button>
        </div>
      )}
      {mode === 'web' ? (
        <div className="login-flow">
          <p>
            {provider === 'monkeycode'
              ? '通过本机登录助手打开独立窗口，完成登录后自动保存。'
              : provider === 'workbuddy_intl'
                ? '在 WorkBuddy AI 国际站官方页面完成授权。账号会加入独立账号池，支持添加多个账号；无需打开桌面端。'
                : '在平台官方页面完成登录，控制台会自动保存账号。'}
          </p>
          {provider === 'qoder' && (
            <p className="field-help">
              选择 Qoder 账号完成授权后会自动添加。可以多次登录添加不同账号，调用时由账号池轮换。
            </p>
          )}
          {provider === 'antigravity' && (
            <p className="field-help">使用 Google 账号授权后加入独立账号池。也可切换“导入凭据”粘贴 refresh token；调用需要服务器能够连接 Google。</p>
          )}
          <button className="button primary wide" onClick={startLogin} disabled={busy || Boolean(flow)}>
            <ExternalLink size={15} />
            {busy ? '正在准备…' : flow ? '等待登录完成…' : '打开登录页面'}
          </button>
          {provider === 'workbuddy_intl' && (
            <p className="field-help">首次使用请打开登录页面。再次登录可以继续添加账号，由账号池自动轮换调用。</p>
          )}
          {flow?.url && (
            <a className="button secondary wide" href={flow.url} target="_blank" rel="noopener noreferrer">
              再次打开登录页面
            </a>
          )}
          {provider === 'antigravity' && flow && (
            <AntigravityCallback key={flow.id} flow={flow} request={request} csrf={csrf} onSaved={onSaved} />
          )}
          {provider === 'monkeycode' && (
            <a className="helper-download" href="/admin/downloads/monkey-login-helper.zip" download>
              首次使用：下载 Windows 登录助手
            </a>
          )}
          {status && <p className="flow-status">{status}</p>}
        </div>
      ) : (
        <form onSubmit={importAccount}>
          <label>凭据文件</label>
          <input type="file" accept=".json,.info,.txt" onChange={loadFile} />
          <label>
            {provider === 'antigravity' ? 'Google refresh token 或 JSON 凭据' : provider === 'monkeycode' ? 'Cookie 或 JSON' : provider === 'qoder' ? 'PAT 或 JSON 凭据' : 'JSON 凭据'}
          </label>
          <textarea
            value={credential}
            onChange={(e) => setCredential(e.target.value)}
            rows="7"
            required
            placeholder={
              provider === 'antigravity' ? '1//… 或 { "refresh_token": "…", "project_id": "…" }' : provider === 'monkeycode'
                ? 'monkeycode_ai_session=…'
                : provider === 'qoder'
                  ? 'pt-… 或 { "uid": "…", "accessToken": "…", "refreshToken": "…" }'
                  : '{ "auth": { … }, "account": { … } }'
            }
          />
          {provider === 'qoder' && (
            <p className="field-help">在所选站点的「设置 → 个人访问令牌」创建 PAT 后粘贴；也可导入旧版 JSON 凭据。</p>
          )}
          <button className="button primary wide" disabled={busy}>
            {busy ? '正在校验…' : '导入并添加'}
          </button>
        </form>
      )}
      {error && <p className="form-error">{error}</p>}
    </Modal>
  )
}

function OpenCodeRuntimeKey({ csrf }) {
  const [status, setStatus] = useState(null)
  const [key, setKey] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  useEffect(() => {
    let mounted = true
    request('unified/opencode-runtime').then((value) => { if (mounted) setStatus(value) })
      .catch((err) => { if (mounted) setError(err.message) })
    return () => { mounted = false }
  }, [])
  async function save(value) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const result = await request('unified/opencode-runtime', {method:'PATCH', body:{api_key:value}, csrf})
      setStatus(result)
      setKey('')
      setMessage(result.key_pending ? '已保存，当前调用结束后的下一次请求将应用。' : value ? '官网 Key 已保存，下次请求自动应用。' : '官网 Key 已清除，恢复免 Key 模式。')
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }
  return <section aria-label="OpenCode 官网凭据">
    <label htmlFor="opencode-official-key">OpenCode 官网 API Key（可选）</label>
    <input id="opencode-official-key" type="password" autoComplete="new-password" value={key}
      onChange={(event) => setKey(event.target.value)} disabled={busy}
      placeholder={status?.key_configured ? '已配置，输入新 Key 可替换' : '留空使用免 Key 模型'} />
    <p className="field-help">所有内置 OpenCode2API 服务共用此 Key，加密保存且不会回显。填写 Key 后可能出现付费模型，请按官网价格选择；保存后可重新读取模型列表。</p>
    <div className="button-row">
      <button className="button secondary" type="button" onClick={() => save(key.trim())} disabled={busy || !key.trim() || !status}>保存官网 Key</button>
      <button className="button secondary" type="button" onClick={() => save('')} disabled={busy || !status?.key_configured}>清除官网 Key</button>
    </div>
    {message && <p className="field-help" role="status">{message}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>
}

function ConnectionDialog({ csrf, initialConnection, onClose, onSaved }) {
  const [form, setForm] = useState(() =>
    initialConnection
      ? {
          name: initialConnection.name,
          id: initialConnection.id,
          base_url: initialConnection.base_url,
          key: '',
          models: (initialConnection.models || []).join('\n'),
          enabled: initialConnection.enabled !== false,
          kind: initialConnection.kind || 'openai',
          opencode_support: initialConnection.opencode_support === true,
        }
      : { name: '', id: '', base_url: '', key: '', models: '', enabled: true, kind: 'openai', opencode_support: false },
  )
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const isRuntime = form.kind === 'opencode_runtime'
  function changeKind(kind) {
    setForm((current) => ({ ...current, kind,
      ...(kind === 'opencode_runtime' ? {name:current.name || 'OpenCode2API', id:current.id || 'opencode-runtime',
        base_url:'', key:'', models:'', opencode_support:false} : {base_url:'',key:''}),
    }))
  }
  const body = () => ({
    name: form.name.trim(),
    id: form.id.trim(),
    base_url: form.base_url.trim(),
    key: form.key.trim(),
    models: form.models
      .split(/\r?\n/)
      .map((v) => v.trim())
      .filter(Boolean),
    enabled: form.enabled,
    kind: form.kind,
    opencode_support: form.opencode_support,
    ...(initialConnection ? { existing_id: initialConnection.id } : {}),
  })
  async function discover() {
    setBusy(true)
    setError('')
    try {
      const result = await request('unified/connections/discover', { method: 'POST', body: body(), csrf })
      update('models', result.models.join('\n'))
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const value = body()
      if (!value.models.length) throw new Error('请填写至少一个模型，或先读取模型列表')
      const path = initialConnection
        ? `unified/connections/${encodeURIComponent(initialConnection.id)}`
        : 'unified/connections'
      await request(path, { method: initialConnection ? 'PATCH' : 'POST', body: value, csrf })
      await onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={initialConnection ? '编辑服务' : '添加服务'} subtitle="OpenAI 兼容接口" onClose={onClose}>
      <form onSubmit={save}>
        <label>服务类型</label>
        <select value={form.kind} onChange={(e) => changeKind(e.target.value)} disabled={Boolean(initialConnection)}>
          <option value="openai">通用 OpenAI 接口</option>
          <option value="opencode_runtime">OpenCode Zen · 轻量直连</option>
          {initialConnection?.kind === 'workbuddy_intl' && <option value="workbuddy_intl">旧版 WorkBuddy 中转</option>}
        </select>
        <div className="field-grid">
          <div>
            <label>服务名称</label>
            <input
              required
              maxLength="60"
              value={form.name}
              onChange={(e) => update('name', e.target.value)}
              placeholder="例如：我的模型服务"
            />
          </div>
          <div>
            <label>模型前缀</label>
            <input
              required
              readOnly={Boolean(initialConnection)}
              pattern={'[a-z][a-z0-9_\\-]{0,39}'}
              value={form.id}
              onChange={(e) => update('id', e.target.value)}
              placeholder="myapi"
            />
          </div>
        </div>
        {initialConnection && (
          <p className="field-help">
            模型前缀保持不变，现有客户端仍可使用 <code>{form.id}/</code>。
          </p>
        )}
        {isRuntime ? (
          <>
            <p className="field-help" role="note">默认轻量直连 OpenCode，无需填写地址或运行客户端。可选配置官网 Key，或直接读取免 Key 模型。</p>
            <OpenCodeRuntimeKey csrf={csrf} />
          </>
        ) : <>
        <label>Base URL</label>
        <input
          type="url"
          required
          value={form.base_url}
          onChange={(e) => update('base_url', e.target.value)}
          placeholder="https://api.example.com/v1"
        />
        <label>API Key</label>
        <input
          type="password"
          required={!initialConnection}
          value={form.key}
          onChange={(e) => update('key', e.target.value)}
          autoComplete="new-password"
          placeholder={initialConnection ? '留空则保持当前 Key' : '服务商提供的 Key'}
        />
        </>}
        <label>模型名称（每行一个）</label>
        <textarea
          rows="5"
          value={form.models}
          onChange={(e) => update('models', e.target.value)}
          placeholder="model-name"
        />
        <button className="button secondary wide" type="button" onClick={discover} disabled={busy}>
          {busy ? '处理中…' : '读取模型列表'}
        </button>
        {!isRuntime && <label className="switch-row">
          <span>
            <strong>启用 OpenCode 支持</strong>
            <small>保留会话信息和客户端标识，缺少时自动补充，仅作用于此服务</small>
          </span>
          <input type="checkbox" checked={form.opencode_support} onChange={(e) => update('opencode_support', e.target.checked)} />
        </label>}
        <label className="switch-row">
          <span>
            <strong>启用服务</strong>
            <small>停用后模型将从目录中隐藏</small>
          </span>
          <input type="checkbox" checked={form.enabled} onChange={(e) => update('enabled', e.target.checked)} />
        </label>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="button primary wide" disabled={busy}>
          {busy ? '正在保存…' : initialConnection ? '保存修改' : '保存服务'}
        </button>
      </form>
    </Modal>
  )
}

function KeyDialog({ csrf, onClose, onSaved }) {
  const [name, setName] = useState('')
  const [created, setCreated] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const result = await request('keys', { method: 'POST', body: { name }, csrf })
      setCreated(result.key)
      await onSaved(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={created ? '保存新密钥' : '创建 API 密钥'} subtitle="客户端访问" onClose={onClose}>
      {created ? (
        <div className="created-key">
          <p>完整密钥只显示一次，请立即复制并保存。</p>
          <code>{created}</code>
          <button className="button primary wide" onClick={() => navigator.clipboard.writeText(created)}>
            <Copy size={15} />
            复制密钥
          </button>
        </div>
      ) : (
        <form onSubmit={save}>
          <label>密钥名称</label>
          <input
            required
            maxLength="60"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="例如：Cherry Studio"
          />
          {error && <p className="form-error">{error}</p>}
          <button className="button primary wide" disabled={busy}>
            {busy ? '正在创建…' : '创建密钥'}
          </button>
        </form>
      )}
    </Modal>
  )
}

function accountEndpoint(account, suffix = '') {
  const id = encodeURIComponent(account.id)
  if (account.provider === 'codebuddy') return `accounts/${id}${suffix}`
  return `unified/${account.provider}/accounts/${id}${suffix}`
}

function AccountRowActions({ account, csrf, onRefresh, onFeedback, onDetails, onTest }) {
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
    function closeForViewportChange() {
      setMore(false)
    }
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
      const path =
        action === 'status'
          ? `unified/accounts/${account.provider}/${encodeURIComponent(account.id)}/balance`
          : accountEndpoint(account, account.provider === 'codebuddy' ? `/actions/${action}` : `/${action}`)
      const result = await request(path, { method: 'POST', body: {}, csrf })
      if (result.ok === false) throw new Error(result.message || '账号操作失败')
      const balance = typeof result.remaining === 'number' ? `，当前${account.quota_unit === '%' ? '额度' : '余额'} ${result.remaining.toLocaleString()}${account.quota_unit || ''}` : ''
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
    setMore((value) => !value)
  }

  const disabled = Boolean(busy) || account.enabled === false
  return (
    <div className="account-actions">
      {account.provider !== 'antigravity' && <button
        className="mini-action"
        onClick={() => run('checkin')}
        disabled={disabled}
        title={account.provider === 'workbuddy_intl' ? '完成新的网页活跃会话后查询余额' : '签到并刷新额度'}
      >
        <Activity size={13} />
        <span>{busy === 'checkin' ? '执行中' : account.provider === 'workbuddy_intl' ? '每日活跃' : '签到'}</span>
      </button>}
      <button className="mini-action" onClick={() => run('refresh')} disabled={disabled} title="刷新凭据和额度">
        <RefreshCw size={13} className={busy === 'refresh' ? 'spin' : ''} />
        <span>{busy === 'refresh' ? '刷新中' : '刷新'}</span>
      </button>
      <button className="mini-action" onClick={() => run('status')} disabled={disabled} title="查询最新余额">
        <WalletCards size={13} />
        <span>{busy === 'status' ? '查询中' : account.quota_unit === '%' ? '额度' : '余额'}</span>
      </button>
      <button
        ref={moreButton}
        className={`dots-action ${more ? 'active' : ''}`}
        onClick={toggleMore}
        aria-label="更多账号操作"
        aria-expanded={more}
      >
        •••
      </button>
      {more &&
        createPortal(
          <div ref={menu} className="account-popover" style={menuPosition} role="menu">
            <button role="menuitem" onClick={() => { setMore(false); onTest(account) }} disabled={account.enabled===false}>
              测试调用
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMore(false)
                onDetails(account)
              }}
            >
              额度明细
            </button>
            <button role="menuitem" onClick={toggleEnabled} disabled={Boolean(busy)}>
              {account.enabled === false ? '启用账号' : '停用账号'}
            </button>
            <button role="menuitem" className="danger-text" onClick={remove} disabled={Boolean(busy)}>
              删除账号
            </button>
          </div>,
          document.body,
        )}
    </div>
  )
}

function Accounts({ data, csrf, onRefresh, onAdd, onImport }) {
  const [query, setQuery] = useState('')
  const [provider, setProvider] = useState('all')
  const [checkingIn, setCheckingIn] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const [details, setDetails] = useState(null)
  const [testAccount, setTestAccount] = useState(null)
  const rows = useMemo(
    () =>
      (data?.accounts || []).filter(
        (account) =>
          (provider === 'all' || accountFilter(account) === provider) &&
          `${account.name || ''} ${account.nickname || ''} ${account.uid || ''}`
            .toLowerCase()
            .includes(query.toLowerCase()),
      ),
    [data, provider, query],
  )

  function showFeedback(account, message, error) {
    setFeedback({ key: `${account.provider}-${account.id}`, message, error })
  }

  async function checkinAll() {
    setCheckingIn(true)
    setFeedback(null)
    try {
      const result = await request('unified/checkin', { method: 'POST', body: {}, csrf })
      const { total, succeeded, failed } = result.summary
      setFeedback({
        key: '',
        error: failed > 0,
        message: total ? `签到完成：成功或已签到 ${succeeded} 个，失败 ${failed} 个` : '没有需要签到的启用账号',
      })
      await onRefresh()
    } catch (error) {
      setFeedback({ key: '', error: true, message: error.message })
    } finally {
      setCheckingIn(false)
    }
  }

  return (
    <section className="content-card">
      <div className="section-head">
        <div>
          <span className="section-label">账号池</span>
          <h2>{data?.accounts?.length || 0} 个账号</h2>
        </div>
        <div className="head-actions">
          <button className="button secondary" onClick={onImport}>
            <Upload size={15} />
            批量导入
          </button>
          <button className="button secondary" onClick={checkinAll} disabled={checkingIn}>
            <Activity size={15} />
            {checkingIn ? '正在签到…' : '全部签到'}
          </button>
          <button className="button primary" onClick={onAdd}>
            <Plus size={15} />
            添加账号
          </button>
        </div>
      </div>
      {feedback && !feedback.key && (
        <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">
          {feedback.message}
        </div>
      )}
      <div className="toolbar">
        <div className="filter-tabs">
          {[['all', '全部'], ...accountFilters].map(([id, label]) => (
            <button key={id} className={provider === id ? 'active' : ''} onClick={() => setProvider(id)}>
              {label}
            </button>
          ))}
        </div>
        <label className="search-box">
          <Search size={15} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索账号" />
        </label>
      </div>
      {
        <DataTable
          columns={['账号', '平台', '状态', '额度', '有效期', '操作']}
          empty={!rows.length && '没有匹配的账号'}
        >
          {rows.map((account) => {
            const key = `${account.provider}-${account.id}`
            const status = account.enabled === false ? 'paused' : account.pool_state || account.status || 'ready'
            const remaining = account.remaining
            return (
              <React.Fragment key={key}>
                <tr>
                  <td>
                    <strong>{account.name || account.nickname || account.uid}</strong>
                    <small>{account.uid || account.id}</small>
                  </td>
                  <td>
                    <span className="provider-badge">{accountPlatform(account)}</span>
                  </td>
                  <td>
                    <StatusBadge status={status} />
                  </td>
                  <td className="tabular">{typeof remaining === 'number' ? remaining.toLocaleString() + (account.quota_unit || '') : '—'}</td>
                  <td>
                    <span className="muted">
                      {account.expires_at ? new Date(account.expires_at).toLocaleDateString('zh-CN') : '未提供'}
                    </span>
                  </td>
                  <td className="row-menu">
                    <AccountRowActions
                      account={account}
                      csrf={csrf}
                      onRefresh={onRefresh}
                      onFeedback={showFeedback}
                      onDetails={setDetails}
                      onTest={setTestAccount}
                    />
                  </td>
                </tr>
                {feedback?.key === key && (
                  <tr className={`account-feedback ${feedback.error ? 'error' : ''}`}>
                    <td colSpan="6">{feedback.message}</td>
                  </tr>
                )}
              </React.Fragment>
            )
          })}
        </DataTable>
      }
      {details && <QuotaDialog account={details} request={request} Modal={Modal} onClose={() => setDetails(null)} />}
      {testAccount && <AccountProbeDialog account={testAccount} models={data?.models || []} csrf={csrf} request={request} Modal={Modal} onClose={() => setTestAccount(null)} />}
    </section>
  )
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
    function closeForViewportChange() {
      setMore(false)
    }
    function closeOnEscape(event) {
      if (event.key === 'Escape') setMore(false)
    }
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
      setMenuPosition({
        top: Math.min(rect.bottom + 7, window.innerHeight - 128),
        left: Math.max(10, rect.right - 142),
      })
    }
    setMore((value) => !value)
  }

  async function toggleEnabled() {
    setMore(false)
    setBusy(true)
    try {
      await request(`unified/connections/${encodeURIComponent(connection.id)}`, {
        method: 'PATCH',
        body: { enabled: !connection.enabled },
        csrf,
      })
      onFeedback(connection.enabled ? '服务已停用' : '服务已启用', false)
      await onRefresh()
    } catch (err) {
      onFeedback(err.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setMore(false)
    if (!window.confirm(`确定删除服务“${connection.name}”吗？引用它的智能路由可能无法使用。`)) return
    setBusy(true)
    try {
      await request(`unified/connections/${encodeURIComponent(connection.id)}`, { method: 'DELETE', csrf })
      onFeedback('服务已删除', false)
      await onRefresh()
    } catch (err) {
      onFeedback(err.message, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        ref={moreButton}
        className={`dots-action ${more ? 'active' : ''}`}
        onClick={toggleMore}
        aria-label={`管理服务 ${connection.name}`}
        aria-expanded={more}
        aria-haspopup="menu"
        disabled={busy}
      >
        •••
      </button>
      {more &&
        createPortal(
          <div ref={menu} className="account-popover connection-popover" style={menuPosition} role="menu">
            <button
              role="menuitem"
              onClick={() => {
                setMore(false)
                onEdit(connection)
              }}
            >
              编辑服务
            </button>
            <button role="menuitem" onClick={toggleEnabled}>
              {connection.enabled ? '停用服务' : '启用服务'}
            </button>
            <button role="menuitem" className="danger-text" onClick={remove}>
              删除服务
            </button>
          </div>,
          document.body,
        )}
    </>
  )
}

function Connections({ data, csrf, onRefresh, onAdd, onEdit }) {
  const [feedback, setFeedback] = useState(null)
  const [pool, setPool] = useState(null)
  const rows = data?.connections || []
  return (
    <section className="content-card">
      <div className="section-head">
        <div>
          <span className="section-label">自定义服务</span>
          <h2>兼容 OpenAI 的接口</h2>
          <p className="muted">使用 Base URL 与 API Key 接入其他服务。WorkBuddy AI 国际站账号请在账号池中添加。</p>
        </div>
        <button className="button primary" onClick={onAdd}>
          <Plus size={15} />
          添加服务
        </button>
      </div>
      {feedback && (
        <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">
          {feedback.message}
        </div>
      )}
      <DataTable columns={['服务', '模型前缀', '状态', '模型数', '操作']} empty={!rows.length && '暂未添加自定义服务'}>
        {rows.map((item) => (
          <tr key={item.id}>
            <td>
              <strong>{item.name}</strong>
              <small>
                {item.kind === 'opencode_runtime' ? 'OpenCode Zen' : item.opencode_support ? 'OpenCode · ' : ''}
                {item.kind === 'workbuddy_intl' ? 'WorkBuddy AI 国际站 · ' : ''}
                {item.kind !== 'opencode_runtime' && item.base_url}
              </small>
            </td>
            <td>
              <code>{item.id}/</code>
            </td>
            <td>
              <StatusBadge status={item.enabled ? 'ready' : 'paused'} />
            </td>
            <td className="tabular">{item.models?.length || 0}</td>
            <td className="row-menu">
              {item.kind !== 'opencode_runtime' && <button className="button secondary pool-open" onClick={() => setPool(item)}>账号池 · {item.accounts?.length || 1}</button>}
              <ConnectionRowActions
                connection={item}
                csrf={csrf}
                onRefresh={onRefresh}
                onEdit={onEdit}
                onFeedback={(message, error) => setFeedback({ message, error })}
              />
            </td>
          </tr>
        ))}
      </DataTable>
      {pool && <ConnectionPoolDialog connection={pool} request={request} csrf={csrf} Modal={Modal} onClose={() => setPool(null)} onSaved={onRefresh} />}
    </section>
  )
}

function Models({ data, onNavigate, csrf, onRefresh }) {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(null)
  const [prefix, setPrefix] = useState('')
  const models = data?.models || []
  const groups = useMemo(() => {
    const counts = new Map()
    for (const model of models) {
      const id = model.id.split('/')[0]
      counts.set(id, (counts.get(id) || 0) + 1)
    }
    const order = ['trae', 'codebuddy', 'workbuddy', 'monkeycode', 'qoder-cn', 'qoder', 'antigravity', 'route']
    return [...order.filter((id) => counts.has(id)), ...[...counts.keys()].filter((id) => !order.includes(id))].map(
      (id) => ({ id, label: providerName(id === 'qoder-cn' ? 'qoder_cn' : id), count: counts.get(id) }),
    )
  }, [models])
  useEffect(() => {
    if (prefix && !groups.some((group) => group.id === prefix)) setPrefix('')
  }, [prefix, groups])
  const search = query.trim().toLowerCase()
  const rows = models.filter(
    (model) =>
      (!prefix || model.id.split('/')[0] === prefix) &&
      `${model.id} ${model.name || ''}`.toLowerCase().includes(search),
  )
  return (
    <section className="content-card">
      <div className="section-head">
        <div>
          <span className="section-label">模型目录</span>
          <h2>{rows.length} 个可用模型</h2>
        </div>
        <label className="search-box">
          <Search size={15} />
          <input
            type="search"
            aria-label="搜索模型"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索模型"
          />
        </label>
      </div>
      <ModelAliasesPanel data={data} request={request} csrf={csrf} onRefresh={onRefresh} />
      <div className="filter-tabs model-filters" role="group" aria-label="模型前缀分类">
        {[{ id: '', label: '全部', count: models.length }, ...groups].map((group) => (
          <button
            type="button"
            key={group.id}
            aria-pressed={prefix === group.id}
            className={prefix === group.id ? 'active' : ''}
            onClick={() => setPrefix(group.id)}
          >
            {group.label}
            <span className="model-filter-count">{group.count}</span>
          </button>
        ))}
      </div>
      <DataTable columns={['模型', '来源', '模型能力', '操作']} empty={!rows.length && '没有匹配的模型'}>
        {rows.map((model) => (
          <tr key={model.id}>
            <td>
              <code>{model.id}</code>
              {model.alias_target && <small className="muted">实际目标：{model.alias_target}</small>}
              {model.catalog_source === 'snapshot' && <small className="muted">官方目录快照</small>}
              {model.available === false && (
                <small className="danger-text">{model.disabled_reason || '当前不可用'}</small>
              )}
            </td>
            <td>
              <span className="provider-badge">{providerName(model.owned_by)}</span>
            </td>
            <td>
              <CapabilityBadges capabilities={model.capabilities} />
            </td>
            <td>
              <div className="head-actions">
                <button className="link-button" aria-label={`模型能力 ${model.id}`} onClick={() => setSelected(model)}>
                  能力
                </button>
                <button className="link-button" onClick={() => onNavigate('test')}>
                  测试
                </button>
              </div>
            </td>
          </tr>
        ))}
      </DataTable>
      {selected && (
        <ModelCapabilitiesDialog
          model={selected}
          request={request}
          Modal={Modal}
          csrf={csrf}
          onClose={() => setSelected(null)}
          onSaved={onRefresh}
        />
      )}
    </section>
  )
}

function KeyRowActions({ apiKey, csrf, onRefresh, onFeedback, onLimits }) {
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
    function closeForViewportChange() {
      setMore(false)
    }
    function closeOnEscape(event) {
      if (event.key === 'Escape') setMore(false)
    }
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
    setMore((value) => !value)
  }

  async function rename(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      await request(`keys/${encodeURIComponent(apiKey.id)}`, { method: 'PATCH', body: { name: name.trim() }, csrf })
      setRenaming(false)
      onFeedback('密钥名称已更新', false)
      await onRefresh()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    setMore(false)
    if (!window.confirm(`确定撤销密钥“${apiKey.name}”吗？使用它的客户端将立即无法访问 API。`)) return
    setBusy(true)
    try {
      await request(`keys/${encodeURIComponent(apiKey.id)}`, { method: 'DELETE', csrf })
      onFeedback('密钥已撤销', false)
      await onRefresh()
    } catch (err) {
      onFeedback(err.message, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button
        ref={moreButton}
        className={`dots-action ${more ? 'active' : ''}`}
        onClick={toggleMore}
        aria-label={`管理密钥 ${apiKey.name}`}
        aria-expanded={more}
        aria-haspopup="menu"
        disabled={busy}
      >
        •••
      </button>
      {more &&
        createPortal(
          <div ref={menu} className="account-popover" style={menuPosition} role="menu">
            <button
              role="menuitem"
              onClick={() => {
                setMore(false)
                setName(apiKey.name)
                setError('')
                setRenaming(true)
              }}
            >
              重命名
            </button>
            <button
              role="menuitem"
              onClick={() => {
                setMore(false)
                onLimits(apiKey)
              }}
            >
              用量限制
            </button>
            <button role="menuitem" className="danger-text" onClick={remove}>
              撤销密钥
            </button>
          </div>,
          document.body,
        )}
      {renaming &&
        createPortal(
          <Modal title="重命名密钥" subtitle="客户端访问" onClose={() => setRenaming(false)}>
            <form onSubmit={rename}>
              <label htmlFor={`key-name-${apiKey.id}`}>密钥名称</label>
              <input
                id={`key-name-${apiKey.id}`}
                autoFocus
                required
                maxLength="60"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
              {error && (
                <p className="form-error" role="alert">
                  {error}
                </p>
              )}
              <button className="button primary wide" disabled={busy}>
                {busy ? '正在保存…' : '保存名称'}
              </button>
            </form>
          </Modal>,
          document.body,
        )}
    </>
  )
}

function Keys({ data, csrf, onRefresh, onAdd }) {
  const [feedback, setFeedback] = useState(null)
  const [selected, setSelected] = useState(null)
  const rows = data?.keys || []
  return (
    <section className="content-card">
      <div className="section-head">
        <div>
          <span className="section-label">访问控制</span>
          <h2>API 密钥</h2>
        </div>
        <button className="button primary" onClick={onAdd}>
          <Plus size={15} />
          创建密钥
        </button>
      </div>
      {feedback && (
        <div className={`result-banner ${feedback.error ? 'error' : ''}`} role="status">
          {feedback.message}
        </div>
      )}
      <DataTable
        columns={['名称', '前缀', '今日 Token', '创建时间', '操作']}
        empty={!rows.length && '暂未创建 API 密钥'}
      >
        {rows.map((k) => (
          <tr key={k.id}>
            <td>
              <strong>{k.name}</strong>
            </td>
            <td>
              <code>{k.hint || (k.prefix ? `${k.prefix}…` : '已隐藏')}</code>
            </td>
            <td>
              <strong title={`${k.usage?.day_tokens || 0} Token`}>{tokenText(k.usage?.day_tokens)}</strong>
              <small>{k.policy?.daily_tokens ? `上限 ${tokenText(k.policy.daily_tokens)}` : '未设上限'}</small>
              {k.policy?.enabled === false && <small className="danger-text">已暂停</small>}
            </td>
            <td className="muted">{k.created ? new Date(k.created * 1000).toLocaleDateString('zh-CN') : '—'}</td>
            <td className="row-menu">
              <KeyRowActions
                apiKey={k}
                csrf={csrf}
                onLimits={setSelected}
                onRefresh={onRefresh}
                onFeedback={(message, error) => setFeedback({ message, error })}
              />
            </td>
          </tr>
        ))}
      </DataTable>
      {selected && (
        <KeyLimitsDialog
          apiKey={selected}
          models={data?.models || []}
          request={request}
          Modal={Modal}
          csrf={csrf}
          onClose={() => setSelected(null)}
          onSaved={async () => {
            await onRefresh()
            setSelected(null)
            setFeedback({ message: '密钥限制已保存', error: false })
          }}
        />
      )}
    </section>
  )
}

function RouteDialog({ data, csrf, initialRoute, onClose, onSaved }) {
  const models = (data?.models || []).filter((model) => !model.id.startsWith('route/') && !model.alias_target)
  const [modelQuery, setModelQuery] = useState('')
  const visibleModels = models.filter((model) =>
    `${model.id} ${model.name || ''}`.toLowerCase().includes(modelQuery.trim().toLowerCase()),
  )
  const [form, setForm] = useState(() =>
    initialRoute
      ? {
          id: initialRoute.id,
          name: initialRoute.name,
          strategy: initialRoute.strategy,
          retries: initialRoute.retries,
          cooldown_seconds: initialRoute.cooldown_seconds,
          max_concurrency: initialRoute.max_concurrency || 8,
          max_account_concurrency: initialRoute.max_account_concurrency || 2,
          targets: [...initialRoute.targets],
          enabled: initialRoute.enabled !== false,
        }
      : {
          id: '',
          name: '',
          strategy: 'priority',
          retries: 2,
          cooldown_seconds: 300,
          targets: [],
          enabled: true,
          max_concurrency: 8,
          max_account_concurrency: 2,
        },
  )
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const update = (key, value) => setForm((current) => ({ ...current, [key]: value }))
  const toggle = (model) =>
    update(
      'targets',
      form.targets.includes(model) ? form.targets.filter((item) => item !== model) : [...form.targets, model],
    )
  const moveTarget = (index, direction) =>
    setForm((current) => {
      const next = [...current.targets]
      const other = index + direction
      if (other < 0 || other >= next.length) return current
      const moved = next[index]
      next[index] = next[other]
      next[other] = moved
      return { ...current, targets: next }
    })
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const path = initialRoute ? `unified/routes/${encodeURIComponent(initialRoute.id)}` : 'unified/routes'
      await request(path, { method: initialRoute ? 'PATCH' : 'POST', body: form, csrf })
      await onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={initialRoute ? '编辑智能路由' : '创建智能路由'} subtitle="模型故障转移" onClose={onClose}>
      <form onSubmit={save}>
        <div className="field-grid">
          <div>
            <label>路由名称</label>
            <input
              required
              maxLength="60"
              value={form.name}
              onChange={(e) => update('name', e.target.value)}
              placeholder="例如：稳定编程模型"
            />
          </div>
          <div>
            <label>调用前缀</label>
            <input
              required
              readOnly={Boolean(initialRoute)}
              pattern={'[a-z][a-z0-9_.\\-]{0,39}'}
              title="小写字母开头，可包含数字、点号、下划线和短横线"
              value={form.id}
              onChange={(e) => update('id', e.target.value)}
              placeholder="glm-5.3"
            />
          </div>
        </div>
        {initialRoute && (
          <p className="field-help">
            调用前缀保持不变，现有客户端可以继续使用 <code>route/{form.id}</code>。
          </p>
        )}
        <div className="field-grid">
          <div>
            <label>选择策略</label>
            <select value={form.strategy} onChange={(e) => update('strategy', e.target.value)}>
              <option value="priority">按顺序优先</option>
              <option value="round_robin">轮询分配</option>
              <option value="latency">优先低延迟</option>
              <option value="smart">自适应：余额、并发与健康</option>
            </select>
          </div>
          <div>
            <label>失败后最多切换</label>
            <input
              type="number"
              min="0"
              max="10"
              value={form.retries}
              onChange={(e) => update('retries', Number(e.target.value))}
            />
          </div>
        </div>
        {form.strategy === 'smart' && (
          <>
            <div className="field-grid">
              <div>
                <label htmlFor="route-concurrency">每个目标的并发上限</label>
                <input
                  id="route-concurrency"
                  type="number"
                  min="1"
                  max="1000"
                  value={form.max_concurrency}
                  onChange={(event) => update('max_concurrency', Number(event.target.value))}
                />
              </div>
              <div>
                <label htmlFor="route-account-concurrency">每个账号的并发上限</label>
                <input
                  id="route-account-concurrency"
                  type="number"
                  min="1"
                  max="100"
                  value={form.max_account_concurrency}
                  onChange={(event) => update('max_account_concurrency', Number(event.target.value))}
                />
              </div>
            </div>
            <p className="field-help">
              跳过额度已耗尽、冷却或并发已满的目标；结合近期成功率与首响应速度分配。不同平台的积分不会直接比较。
            </p>
          </>
        )}
        <label>失败冷却时间（秒）</label>
        <input
          type="number"
          min="10"
          max="86400"
          value={form.cooldown_seconds}
          onChange={(e) => update('cooldown_seconds', Number(e.target.value))}
        />
        <label htmlFor="route-model-search">目标模型（按选择顺序）</label>
        <div className="model-picker-search">
          <Search size={15} />
          <input
            id="route-model-search"
            type="search"
            value={modelQuery}
            onChange={(event) => setModelQuery(event.target.value)}
            placeholder="输入模型名称快速筛选"
            autoComplete="off"
          />
        </div>
        <div className="model-picker">
          {visibleModels.length ? (
            visibleModels.map((model) => (
              <label key={model.id} className={form.targets.includes(model.id) ? 'selected' : ''}>
                <input type="checkbox" checked={form.targets.includes(model.id)} onChange={() => toggle(model.id)} />
                <code>{model.id}</code>
              </label>
            ))
          ) : (
            <p className="model-picker-empty">没有匹配的模型</p>
          )}
        </div>
        {form.targets.length > 0 && (
          <div className="route-target-order" aria-label="目标模型顺序">
            {form.targets.map((target, index) => (
              <div key={target}>
                <span>{index + 1}</span>
                <code title={target}>{target}</code>
                <button
                  type="button"
                  onClick={() => moveTarget(index, -1)}
                  disabled={index === 0}
                  aria-label={`上移 ${target}`}
                >
                  ↑
                </button>
                <button
                  type="button"
                  onClick={() => moveTarget(index, 1)}
                  disabled={index === form.targets.length - 1}
                  aria-label={`下移 ${target}`}
                >
                  ↓
                </button>
              </div>
            ))}
          </div>
        )}
        <label className="switch-row">
          <span>
            <strong>启用路由</strong>
            <small>停用后，客户端将无法调用这个路由</small>
          </span>
          <input type="checkbox" checked={form.enabled} onChange={(e) => update('enabled', e.target.checked)} />
        </label>
        <p className="field-help">
          客户端使用 <code>route/{form.id || '路由前缀'}</code>。上游失败时会按策略自动尝试下一个目标。
        </p>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button className="button primary wide" disabled={busy || !form.targets.length}>
          {busy ? '正在保存…' : initialRoute ? '保存修改' : '创建路由'}
        </button>
      </form>
    </Modal>
  )
}

function Routes({ data, csrf, onRefresh, onAdd, onEdit }) {
  const rows = data?.routes || []
  const [message, setMessage] = useState('')
  const [preview, setPreview] = useState(null)
  async function remove(id) {
    if (!window.confirm(`删除路由 route/${id}？`)) return
    try {
      await request(`unified/routes/${encodeURIComponent(id)}`, { method: 'DELETE', body: {}, csrf })
      setMessage('路由已删除')
      await onRefresh()
    } catch (err) {
      setMessage(err.message)
    }
  }
  return (
    <div className="stack">
      {message && <div className="result-banner">{message}</div>}
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">统一入口</span>
            <h2>智能路由</h2>
            <p className="muted">为多个真实模型创建一个稳定别名，并在失败时自动切换。</p>
          </div>
          <button className="button primary" onClick={onAdd}>
            <Plus size={15} />
            创建路由
          </button>
        </div>
        <DataTable columns={['路由', '策略', '目标模型', '健康状态', '']} empty={!rows.length && '暂未创建智能路由'}>
          {rows.map((route) => {
            const cooling = Object.values(route.health || {}).filter(
              (item) => item.cooldown_until * 1000 > Date.now(),
            ).length
            return (
              <tr key={route.id}>
                <td>
                  <strong>{route.name}</strong>
                  <small>
                    <code>route/{route.id}</code>
                  </small>
                </td>
                <td>
                  {{ priority: '顺序优先', round_robin: '轮询', latency: '低延迟', smart: '自适应' }[route.strategy]}
                </td>
                <td>
                  <strong>{route.targets.length} 个</strong>
                  <small>{route.targets.join(' → ')}</small>
                </td>
                <td>
                  <StatusBadge status={route.enabled === false ? 'paused' : cooling ? 'cooling' : 'ready'} />
                  <small>
                    {route.enabled === false ? '路由已停用' : cooling ? `${cooling} 个目标冷却中` : '全部可参与路由'}
                  </small>
                </td>
                <td className="row-menu">
                  <div className="route-row-actions">
                    <button className="plan-open" onClick={() => setPreview(route)} disabled={route.enabled === false}><Activity size={14} />预览</button>
                    <button
                      type="button"
                      onClick={() => onEdit(route)}
                      aria-label={`编辑路由 ${route.name}`}
                      title="编辑路由"
                    >
                      <Pencil size={14} />
                    </button>
                    <button
                      type="button"
                      className="danger-icon"
                      onClick={() => remove(route.id)}
                      aria-label={`删除路由 ${route.name}`}
                      title="删除路由"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </td>
              </tr>
            )
          })}
        </DataTable>
      </section>
      <NetworkPanel request={request} csrf={csrf} />
      {preview && <RoutePlanDialog route={preview} request={request} Modal={Modal} onClose={() => setPreview(null)} />}
    </div>
  )
}

function Logs({ csrf }) {
  const [state, setState] = useState({ items: [], total: 0, summary: {} })
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState('')
  async function load() {
    setLoading(true)
    try {
      setState(await request(`unified/logs?limit=100&model=${encodeURIComponent(filter)}`))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
  }, [])
  async function clear() {
    if (!window.confirm('清空全部调用记录？')) return
    await request('unified/logs', { method: 'DELETE', body: {}, csrf })
    await load()
  }
  const summary = state.summary || {}
  return (
    <div className="stack">
      <section className="summary-grid compact-summary">
        {[
          ['24 小时请求', summary.count || 0],
          ['成功率', summary.success_rate == null ? '—' : `${summary.success_rate}%`],
          ['平均耗时', summary.avg_duration_ms ? `${Math.round(summary.avg_duration_ms)} ms` : '—'],
          ['推理 Token', (summary.reasoning_tokens || 0).toLocaleString()],
        ].map(([label, value]) => (
          <article className="summary-card" key={label}>
            <div>
              <strong>{value}</strong>
              <span>{label}</span>
            </div>
          </article>
        ))}
      </section>
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">最近 90 天</span>
            <h2>调用记录</h2>
            <p className="muted">不保存提示词、回复正文或密钥。</p>
          </div>
          <div className="head-actions">
            <button className="button secondary" onClick={clear}>
              <Trash2 size={14} />
              清空
            </button>
            <button className="button secondary" onClick={load}>
              <RefreshCw size={14} className={loading ? 'spin' : ''} />
              刷新
            </button>
          </div>
        </div>
        <div className="toolbar">
          <label className="search-box">
            <Search size={15} />
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && load()}
              placeholder="模型或请求 ID"
            />
          </label>
          <span className="muted">共 {state.total || 0} 条</span>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <DataTable
          columns={['时间', '模型', '实际目标与账号', '结果', '首响应 / 总耗时', 'Token', '操作']}
          empty={!state.items.length && '暂无调用记录'}
        >
          {state.items.map((item) => (
            <tr key={item.id}>
              <td className="muted">{new Date(item.created * 1000).toLocaleString('zh-CN')}</td>
              <td>
                <code>{item.model || '—'}</code>
              </td>
              <td>
                <small>{item.resolved_model || '—'}</small>
                <small className="muted">{item.account_name || item.account_id || ''}</small>
              </td>
              <td>
                <StatusBadge status={item.ok ? 'ready' : 'error'} />
                <small>{item.status || item.outcome}</small>
                {item.diagnosis && <small className="muted">{item.diagnosis.label}{item.upstream_code ? ` · ${item.upstream_code}` : ''}</small>}
              </td>
              <td className="tabular">
                {item.ttft_ms == null ? '—' : `${item.ttft_ms.toLocaleString()} ms`}
                <small className="muted">总计 {item.duration_ms.toLocaleString()} ms</small>
              </td>
              <td>
                <strong>{(item.prompt_tokens + item.completion_tokens).toLocaleString()}</strong>
              </td>
              <td>
                {item.request_id ? (
                  <button
                    className="link-button"
                    aria-label={`诊断 ${item.request_id}`}
                    onClick={() => setSelected(item.request_id)}
                  >
                    诊断{item.attempt_count > 1 ? ` · ${item.attempt_count} 次尝试` : ''}
                  </button>
                ) : (
                  <small className="muted">历史记录</small>
                )}
              </td>
            </tr>
          ))}
        </DataTable>
        {selected && (
          <RequestDetailsDialog
            requestId={selected}
            request={request}
            Modal={Modal}
            onClose={() => setSelected(null)}
          />
        )}
      </section>
    </div>
  )
}

const tokenCount = (value) => Number(value || 0)
const exactCount = (value) => tokenCount(value).toLocaleString('zh-CN')
const tokenText = (value) => {
  const count = tokenCount(value)
  if (!count) return '0'
  if (count < 10000) return '<0.01 M'
  const large = count >= 100000000
  const amount = count / (large ? 100000000 : 1000000)
  return `${amount.toLocaleString('zh-CN', { maximumFractionDigits: 2 })} ${large ? '亿' : 'M'}`
}
const totalTokens = (row) => tokenCount(row?.prompt_tokens) + tokenCount(row?.completion_tokens)
const dateKey = (date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
const parseLocalDay = (key) => new Date(`${key}T00:00:00`)

function Usage({ settings, refreshNonce }) {
  const [state, setState] = useState({ daily: [], models: [], total: {}, generated_at: 0 })
  const [period, setPeriod] = useState('daily')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [hoverDay, setHoverDay] = useState('')
  async function load() {
    setLoading(true)
    setError('')
    try {
      setState(await request('unified/usage'))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => {
    load()
  }, [settings?.retention_days, refreshNonce])

  const dailyMap = useMemo(() => new Map((state.daily || []).map((row) => [row.day, row])), [state.daily])
  const retentionDays = Math.min(365, Math.max(30, Number(settings?.retention_days || state.retention_days || 365)))
  const heatmap = useMemo(() => {
    const today = new Date()
    today.setHours(0, 0, 0, 0)
    const cutoff = new Date(today)
    cutoff.setDate(cutoff.getDate() - retentionDays + 1)
    const first = new Date(cutoff)
    first.setDate(first.getDate() - ((first.getDay() + 6) % 7))
    const weeks = []
    for (let cursor = new Date(first); cursor <= today; cursor.setDate(cursor.getDate() + 7)) {
      const week = []
      for (let weekday = 0; weekday < 7; weekday += 1) {
        const day = new Date(cursor)
        day.setDate(cursor.getDate() + weekday)
        const key = dateKey(day)
        week.push({ key, visible: day >= cutoff && day <= today, row: dailyMap.get(key), day })
      }
      weeks.push(week)
    }
    const max = Math.max(0, ...state.daily.map(totalTokens))
    const monthLabels = weeks
      .map((week, index) => {
        const firstVisible = week.find((cell) => cell.visible)
        return firstVisible && (index === 0 || firstVisible.day.getDate() <= 7)
          ? { index, label: `${firstVisible.day.getMonth() + 1}月` }
          : null
      })
      .filter(Boolean)
    return { weeks, max, monthLabels }
  }, [dailyMap, retentionDays, state.daily])

  const todayKey = dateKey(new Date())
  const firstRecordedAt = state.total?.first_recorded_at
  const summary = useMemo(() => {
    const sumSince = (days) => {
      const from = new Date()
      from.setHours(0, 0, 0, 0)
      from.setDate(from.getDate() - days + 1)
      return state.daily.filter((row) => row.day >= dateKey(from)).reduce((sum, row) => sum + totalTokens(row), 0)
    }
    return {
      today: totalTokens(dailyMap.get(todayKey)),
      week: sumSince(7),
      month: sumSince(30),
      total: totalTokens(state.total),
    }
  }, [dailyMap, state.daily, state.total, todayKey])

  const trend = useMemo(() => {
    const byDay = new Map((state.daily || []).map((row) => [row.day, row]))
    if (period === 'daily') {
      const end = new Date()
      end.setHours(0, 0, 0, 0)
      return Array.from({ length: Math.min(30, retentionDays) }, (_, index) => {
        const day = new Date(end)
        day.setDate(end.getDate() - (Math.min(30, retentionDays) - index - 1))
        const key = dateKey(day)
        const row = byDay.get(key) || {}
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
      const item = grouped.get(key) || {
        key,
        label: `${day.getMonth() + 1}${period === 'weekly' ? `/${day.getDate()}` : '月'}`,
        tokens: 0,
      }
      item.tokens += totalTokens(row)
      grouped.set(key, item)
    }
    if (period === 'total') {
      const now = new Date()
      const monthCount = Math.min(12, Math.ceil(retentionDays / 30.4) + 1)
      const months = Array.from({ length: monthCount }, (_, index) => {
        const month = new Date(now.getFullYear(), now.getMonth() - (monthCount - index - 1), 1)
        const key = dateKey(month)
        return grouped.get(key) || { key, label: `${month.getMonth() + 1}月`, tokens: 0 }
      })
      let cumulative = 0
      return months.map((item) => ({ ...item, tokens: (cumulative += item.tokens) }))
    }
    const count = Math.min(12, Math.ceil(retentionDays / 7))
    const currentMonday = new Date()
    currentMonday.setHours(0, 0, 0, 0)
    currentMonday.setDate(currentMonday.getDate() - ((currentMonday.getDay() + 6) % 7))
    return Array.from({ length: count }, (_, index) => {
      const week = new Date(currentMonday)
      week.setDate(currentMonday.getDate() - (count - index - 1) * 7)
      const key = dateKey(week)
      return grouped.get(key) || { key, label: `${week.getMonth() + 1}/${week.getDate()}`, tokens: 0 }
    })
  }, [period, retentionDays, state.daily])

  const chartModels = useMemo(() => {
    const palette = ['#368dcc', '#37a978', '#8666d9', '#ed6668', '#d4a33b', '#34a5ad']
    const ranked = (state.models || [])
      .map((row) => ({ ...row, tokens: totalTokens(row) }))
      .sort((a, b) => b.tokens - a.tokens)
    const visible = ranked.slice(0, 5).map((row, index) => ({ ...row, color: palette[index] }))
    const rest = ranked.slice(5)
    if (rest.length)
      visible.push({
        model: '其他模型',
        tokens: rest.reduce((sum, row) => sum + row.tokens, 0),
        requests: rest.reduce((sum, row) => sum + tokenCount(row.requests), 0),
        color: palette[5],
      })
    return visible
  }, [state.models])
  const modelTotal = chartModels.reduce((sum, item) => sum + item.tokens, 0)
  const circumference = 2 * Math.PI * 56
  let accumulated = 0
  const ring = chartModels.map((item) => {
    const length = modelTotal ? (item.tokens / modelTotal) * circumference : 0
    const segment = { ...item, length, offset: accumulated }
    accumulated += length
    return segment
  })
  const maxTrend = Math.max(1, ...trend.map((item) => item.tokens))

  return (
    <div className="stack usage-page">
      <section className="summary-grid usage-summary">
        {[
          ['今日 Token', summary.today],
          ['近 7 日', summary.week],
          ['近 30 日', summary.month],
          ['累计 Token', summary.total],
        ].map(([label, value]) => (
          <article className="summary-card" key={label}>
            <div>
              <strong title={`${exactCount(value)} Token`}>{tokenText(value)}</strong>
              <span>{label}</span>
            </div>
          </article>
        ))}
      </section>
      <section className="content-card usage-heatmap-card">
        <div className="section-head">
          <div>
            <span className="section-label">活动日历</span>
            <h2>Token 消耗热力图</h2>
            <p className="muted">每格代表一天；颜色越深，当天消耗越多。当前保留 {retentionDays} 天。</p>
          </div>
          <button className="button secondary" onClick={load} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : ''} />
            刷新
          </button>
        </div>
        {error && <div className="result-banner error">统计加载失败：{error}</div>}
        <div className="heatmap-scroll" aria-label="每日 Token 消耗日历">
          <div className="heatmap-days">
            <span>一</span>
            <span></span>
            <span>三</span>
            <span></span>
            <span>五</span>
            <span></span>
            <span></span>
          </div>
          <div className="heatmap-body">
            <div className="heatmap-months" style={{ gridTemplateColumns: `repeat(${heatmap.weeks.length}, 12px)` }}>
              {heatmap.monthLabels.map((item) => (
                <span key={item.index} style={{ gridColumn: item.index + 1 }}>
                  {item.label}
                </span>
              ))}
            </div>
            <div className="heatmap-weeks" style={{ gridTemplateColumns: `repeat(${heatmap.weeks.length}, 12px)` }}>
              {heatmap.weeks.map((week, index) => (
                <div className="heatmap-week" key={index}>
                  {week.map((cell) => {
                    const count = totalTokens(cell.row)
                    const level = count
                      ? Math.min(4, Math.max(1, Math.ceil((count / Math.max(1, heatmap.max)) * 4)))
                      : 0
                    const description = `${cell.key} · ${tokenText(count)} Token · ${cell.row?.requests || 0} 次请求`
                    return (
                      <span
                        key={cell.key}
                        className={`heat-cell level-${level} ${cell.visible ? '' : 'outside'}`}
                        title={
                          cell.visible
                            ? `${cell.key} · ${exactCount(count)} Token · ${cell.row?.requests || 0} 次请求`
                            : ''
                        }
                        aria-label={cell.visible ? description : undefined}
                        onMouseEnter={() => cell.visible && setHoverDay(description)}
                        onMouseLeave={() => setHoverDay('')}
                      />
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="heatmap-footer">
          <span className="muted">{hoverDay || '将鼠标移到方格查看当天详情'}</span>
          <span className="heat-legend">
            <small>少</small>
            {[0, 1, 2, 3, 4].map((level) => (
              <i key={level} className={`heat-cell level-${level}`} />
            ))}
            <small>多</small>
          </span>
        </div>
        {firstRecordedAt && (
          <p className="muted usage-footnote">
            从 {new Date(firstRecordedAt * 1000).toLocaleString('zh-CN')} 开始记录；此前的调用没有历史 Token
            记录，无法回填到统计页。
          </p>
        )}
      </section>
      <div className="usage-lower-grid">
        <section className="content-card trend-card">
          <div className="section-head">
            <div>
              <span className="section-label">消耗趋势</span>
              <h2>{period === 'daily' ? '每日 Token' : period === 'weekly' ? '每周 Token' : '按月累计 Token'}</h2>
            </div>
            <div className="filter-tabs" role="tablist" aria-label="统计周期">
              {[
                ['daily', '每日'],
                ['weekly', '每周'],
                ['total', '累计'],
              ].map(([id, label]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={period === id}
                  className={period === id ? 'active' : ''}
                  onClick={() => setPeriod(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <div className="trend-chart" role="img" aria-label="Token 消耗趋势柱状图">
            {trend.length ? (
              trend.map((item, index) => (
                <div className="trend-column" key={item.key} title={`${item.label} · ${exactCount(item.tokens)} Token`}>
                  <span className="trend-value">{tokenText(item.tokens)}</span>
                  <i style={{ height: `${Math.max(item.tokens ? 3 : 0, (item.tokens / maxTrend) * 100)}%` }} />
                  <small>
                    {index === 0 || index === trend.length - 1 || index % Math.max(1, Math.ceil(trend.length / 8)) === 0
                      ? item.label
                      : ''}
                  </small>
                </div>
              ))
            ) : (
              <Empty>暂无统计数据，产生调用后会显示 Token 用量</Empty>
            )}
          </div>
          <p className="muted usage-footnote">
            用量取自上游返回的 Token 统计；若上游未返回用量字段，该次请求会显示为 0。
          </p>
        </section>
        <section className="content-card model-usage-card">
          <div className="section-head">
            <div>
              <span className="section-label">模型分布</span>
              <h2>按模型统计</h2>
            </div>
          </div>
          {modelTotal ? (
            <div className="model-usage-content">
              <div className="donut-wrap">
                <svg viewBox="0 0 144 144" role="img" aria-label={`累计 ${exactCount(modelTotal)} Token`}>
                  <circle className="donut-track" cx="72" cy="72" r="56" />
                  <g transform="rotate(-90 72 72)">
                    {ring.map((item) => (
                      <circle
                        key={item.model}
                        cx="72"
                        cy="72"
                        r="56"
                        fill="none"
                        stroke={item.color}
                        strokeWidth="22"
                        strokeDasharray={`${item.length} ${circumference - item.length}`}
                        strokeDashoffset={-item.offset}
                      />
                    ))}
                  </g>
                </svg>
                <div>
                  <strong title={`${exactCount(modelTotal)} Token`}>{tokenText(modelTotal)}</strong>
                  <span>Token</span>
                </div>
              </div>
              <div className="model-usage-list">
                {chartModels.map((item) => (
                  <div className="model-usage-row" key={item.model}>
                    <div>
                      <i style={{ background: item.color }} />
                      <code title={item.model}>{item.model}</code>
                      <strong>{modelTotal ? `${((item.tokens / modelTotal) * 100).toFixed(1)}%` : '0%'}</strong>
                    </div>
                    <small title={`${exactCount(item.tokens)} Token`}>
                      {tokenText(item.tokens)} Token · {exactCount(item.requests)} 次请求
                    </small>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <Empty>{loading ? '正在读取用量…' : '暂无 Token 统计数据'}</Empty>
          )}
        </section>
      </div>
    </div>
  )
}

function SettingsPage({ settings, theme, onThemeChange, onSave, onNavigate }) {
  const [form, setForm] = useState(settings)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  useEffect(() => setForm(settings), [settings])
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setMessage('')
    if (
      Number(form.retention_days) < Number(settings.retention_days) &&
      !window.confirm(`缩短保留时间会立即删除早于 ${form.retention_days} 天的统计记录，是否继续？`)
    ) {
      setBusy(false)
      return
    }
    try {
      await onSave(form)
      setMessage('设置已保存')
    } catch (err) {
      setMessage(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="settings-grid console-settings">
      <section className="content-card">
        <span className="section-label">外观</span>
        <h2>控制台主题</h2>
        <p className="muted settings-copy">主题只保存在当前浏览器中，不影响其他用户。</p>
        <div className="theme-options">
          {[
            ['light', Sun, '浅色'],
            ['dark', Moon, '深色'],
            ['system', Monitor, '跟随系统'],
          ].map(([id, Icon, label]) => (
            <button
              type="button"
              key={id}
              className={`theme-option ${theme === id ? 'active' : ''}`}
              data-theme-value={id}
              aria-pressed={theme === id}
              onClick={(event) => onThemeChange(id, event)}
            >
              <Icon size={18} />
              <span>{label}</span>
              {theme === id && <Check size={15} />}
            </button>
          ))}
        </div>
      </section>
      <form className="content-card" onSubmit={save}>
        <span className="section-label">数据与体验</span>
        <h2>统计与刷新</h2>
        <label htmlFor="retention-days">调用统计保留时间</label>
        <select
          id="retention-days"
          value={form.retention_days || 365}
          onChange={(e) => setForm((current) => ({ ...current, retention_days: Number(e.target.value) }))}
        >
          {[30, 90, 180, 365].map((days) => (
            <option key={days} value={days}>
              {days} 天
            </option>
          ))}
        </select>
        <p className="settings-copy muted">
          只保留请求时间、模型、状态、耗时和 Token 数；不会保存提示词或回复正文。当前数据最多可保留 365 天。
        </p>
        <label htmlFor="auto-refresh">自动刷新间隔</label>
        <select
          id="auto-refresh"
          value={form.auto_refresh_seconds || 0}
          onChange={(e) => setForm((current) => ({ ...current, auto_refresh_seconds: Number(e.target.value) }))}
        >
          <option value="0">关闭</option>
          <option value="30">30 秒</option>
          <option value="60">1 分钟</option>
          <option value="300">5 分钟</option>
        </select>
        <p className="settings-copy muted">开启后，控制台会按间隔刷新账号状态和统计数据。</p>
        <button className="button primary wide" disabled={busy}>
          <Save size={15} />
          {busy ? '正在保存…' : '保存设置'}
        </button>
        {message && (
          <div className={`result-banner settings-message ${message !== '设置已保存' ? 'error' : ''}`}>{message}</div>
        )}
      </form>
      <section className="content-card settings-wide">
        <span className="section-label">版本管理</span>
        <h2>升级与回滚</h2>
        <p className="muted settings-copy">检查仓库新版本，保留现有账号和密钥进行升级。新版本健康检查失败自动恢复。</p>
        <button className="button secondary" type="button" onClick={() => onNavigate('releases')}>
          <Download size={14} />
          管理版本
        </button>
      </section>
      <section className="content-card settings-wide">
        <span className="section-label">数据说明</span>
        <h2>Token 统计如何计算</h2>
        <p className="muted settings-copy">
          统计从每次 API 返回的 usage 字段读取输入和输出
          Token。更换保留时间后，超出范围的历史统计会清理；已清理的数据无法恢复。累计数字表示当前保留范围内的用量。
        </p>
        <button className="button secondary" type="button" onClick={() => onNavigate('logs')}>
          <FileText size={14} />
          查看调用记录
        </button>
      </section>
    </div>
  )
}

function Automation(props) {
  return <AutomationCenter {...props} request={request} />
}

function Backup({ csrf }) {
  const [password, setPassword] = useState('')
  const [includeLogs, setIncludeLogs] = useState(false)
  const [file, setFile] = useState(null)
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  async function exportData() {
    setBusy('export')
    setMessage('')
    try {
      const response = await fetch('/admin/api/unified/backup/export', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ password, include_logs: includeLogs }),
      })
      if (!response.ok) {
        const value = await response.json().catch(() => ({}))
        throw new Error(value.detail || '导出失败')
      }
      const blob = await response.blob()
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `unified2api-${new Date().toISOString().slice(0, 10)}.ubak`
      link.click()
      URL.revokeObjectURL(url)
      setMessage('加密备份已下载')
    } catch (err) {
      setMessage(err.message)
    } finally {
      setBusy('')
    }
  }
  async function importData() {
    if (!file || !window.confirm('恢复会替换当前账号和设置，服务随后会自动重启。继续吗？')) return
    setBusy('import')
    setMessage('正在校验并恢复备份…')
    try {
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(reader.result)
        reader.onerror = reject
        reader.readAsDataURL(file)
      })
      const result = await request('unified/backup/import', {
        method: 'POST',
        body: { password, data: String(dataUrl).split(',')[1] },
        csrf,
      })
      setMessage(result.restarting ? '恢复成功，服务正在重启，请稍后刷新页面' : '恢复成功，请重启服务后使用')
    } catch (err) {
      setMessage(err.message)
    } finally {
      setBusy('')
    }
  }
  return (
    <div className="settings-grid">
      <section className="content-card">
        <span className="section-label">导出</span>
        <h2>创建加密备份</h2>
        <p className="muted backup-copy">包含 TRAE、CodeBuddy、MonkeyCode 账号、自定义服务、路由和自动任务设置。</p>
        <label>备份密码</label>
        <input
          type="password"
          minLength="8"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="至少 8 个字符"
        />
        <label className="switch-row">
          <span>
            <strong>包含调用记录</strong>
            <small>可能明显增大备份文件</small>
          </span>
          <input type="checkbox" checked={includeLogs} onChange={(e) => setIncludeLogs(e.target.checked)} />
        </label>
        <button className="button primary wide" onClick={exportData} disabled={busy || password.length < 8}>
          <Download size={15} />
          {busy === 'export' ? '正在打包…' : '下载加密备份'}
        </button>
      </section>
      <section className="content-card">
        <span className="section-label">恢复</span>
        <h2>导入备份</h2>
        <p className="muted backup-copy">
          仅接受 Unified2API 的 <code>.ubak</code> 加密文件。恢复成功后容器自动重启。
        </p>
        <label>备份文件</label>
        <input
          type="file"
          accept=".ubak,application/octet-stream"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
        <label>备份密码</label>
        <input
          type="password"
          minLength="8"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="创建备份时使用的密码"
        />
        <button className="button secondary wide" onClick={importData} disabled={busy || !file || password.length < 8}>
          <Upload size={15} />
          {busy === 'import' ? '正在恢复…' : '恢复并重启服务'}
        </button>
      </section>
      {message && <div className="result-banner settings-message">{message}</div>}
    </div>
  )
}

function TestPanel({ data, csrf }) {
  const models = (data?.models || []).map((item) => item.id)
  const [model, setModel] = useState('')
  const [message, setMessage] = useState('请只回复：连接成功')
  const [output, setOutput] = useState('')
  const [failed, setFailed] = useState(false)
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    if (!models.includes(model)) {
      setFailed(true)
      setOutput('请从建议列表中选择有效模型')
      return
    }
    setBusy(true)
    setFailed(false)
    setOutput('正在等待上游响应…')
    try {
      const result = await request('unified/test', { method: 'POST', body: { model, message }, csrf })
      setFailed(!result.ok)
      setOutput(result.ok ? result.answer : result.error || '调用失败，上游未提供错误详情')
    } catch (err) {
      setFailed(true)
      setOutput(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="test-layout">
      <form className="content-card" onSubmit={submit}>
        <span className="section-label">请求</span>
        <h2>调用测试</h2>
        <label htmlFor="test-model">模型</label>
        <input
          id="test-model"
          list="test-model-options"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="输入模型名称搜索"
          autoComplete="off"
          required
        />
        <datalist id="test-model-options">
          {models.map((id) => (
            <option key={id} value={id} />
          ))}
        </datalist>
        <label htmlFor="test-message">消息</label>
        <textarea
          id="test-message"
          rows="7"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          maxLength="16000"
          required
        />
        <button className="button primary wide" disabled={busy}>
          {busy ? '正在调用…' : '发送请求'}
        </button>
      </form>
      <section className="content-card response-panel">
        <span className="section-label">响应</span>
        <h2>{failed ? '调用失败' : '模型回复'}</h2>
        {output ? (
          <pre className={`response-output ${failed ? 'form-error' : ''}`} role={failed ? 'alert' : 'status'}>
            {output}
          </pre>
        ) : (
          <Empty>发送请求后，结果会显示在这里</Empty>
        )}
      </section>
    </div>
  )
}

function App() {
  const [csrf, setCsrf] = useState('')
  const [data, setData] = useState(null)
  const [page, setPage] = useState('overview')
  const [loading, setLoading] = useState(true)
  const [authenticated, setAuthenticated] = useState(true)
  const [drawer, setDrawer] = useState(false)
  const drawerRef = useRef(null)
  const mobile = useMedia('(max-width:800px)')
  const [loadError, setLoadError] = useState('')
  const [dialog, setDialog] = useState(null)
  const [editingConnection, setEditingConnection] = useState(null)
  const [editingRoute, setEditingRoute] = useState(null)
  const [consoleSettings, setConsoleSettings] = useState({ retention_days: 365, auto_refresh_seconds: 0 })
  const { theme, resolvedTheme, setTheme } = useTheme()
  const [refreshNonce, setRefreshNonce] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [overview, session, preferences] = await Promise.all([
        request('unified/overview'),
        request('session'),
        request('unified/settings'),
      ])
      setData({ ...overview, models: uniqueModels(overview.models) })
      setCsrf(session.csrf)
      setConsoleSettings(preferences.settings)
      setAuthenticated(true)
      setRefreshNonce((value) => value + 1)
    } catch (err) {
      if (err.status === 401) setAuthenticated(false)
      else setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])
  useEffect(() => {
    if (!mobile) setDrawer(false)
  }, [mobile])
  useEffect(() => {
    const interval = Number(consoleSettings.auto_refresh_seconds || 0)
    if (!authenticated || !interval) return undefined
    const timer = window.setInterval(() => {
      void load()
    }, interval * 1000)
    return () => window.clearInterval(timer)
  }, [authenticated, consoleSettings.auto_refresh_seconds, load])
  async function login(key) {
    const result = await request('login', { method: 'POST', body: { key } })
    setCsrf(result.csrf)
    setAuthenticated(true)
    await load()
  }
  async function logout() {
    await request('logout', { method: 'POST', body: {}, csrf })
    setData(null)
    setAuthenticated(false)
  }
  function navigate(next) {
    setPage(next)
    setDrawer(false)
  }
  async function saved(keepOpen = false) {
    await load()
    if (!keepOpen) setDialog(null)
  }
  async function saveConsoleSettings(form) {
    const result = await request('unified/settings', { method: 'PATCH', body: form, csrf })
    setConsoleSettings(result.settings)
  }

  if (loading && !data)
    return (
      <div className="boot">
        <RefreshCw className="spin" size={20} />
        <span>正在连接控制台</span>
      </div>
    )
  const themeControl = <ThemeToggle resolvedTheme={resolvedTheme} onChange={setTheme} />
  if (!authenticated) return <Login onLogin={login} themeControl={themeControl} />
  if (!data && loadError)
    return (
      <main className="login-shell">
        <section className="login-panel glass-panel">
          {themeControl}
          <h1>连接暂时失败</h1>
          <p className="form-error" role="alert">
            {loadError}
          </p>
          <button className="button primary" onClick={load}>
            重新连接
          </button>
        </section>
      </main>
    )
  const [title, description] = pageMeta[page]
  const pages = {
    overview: <Dashboard data={data} onNavigate={navigate} request={request} />,
    accounts: (
      <div className="stack reliability-stack">
      <Accounts
        data={data}
        csrf={csrf}
        onRefresh={load}
        onAdd={() => setDialog('account')}
        onImport={() => setDialog('import')}
      />
      <CreditPolicyPanel data={data} request={request} csrf={csrf} />
      </div>
    ),
    tasks: <TasksPage data={data} request={request} csrf={csrf} onRefresh={load} />,
    performance: <PerformancePage request={request} DataTable={DataTable} />,
    clients: <ClientsPage data={data} request={request} csrf={csrf} />,
    connections: (
      <Connections
        data={data}
        csrf={csrf}
        onRefresh={load}
        onAdd={() => {
          setEditingConnection(null)
          setDialog('connection')
        }}
        onEdit={(connection) => {
          setEditingConnection(connection)
          setDialog('connection')
        }}
      />
    ),
    models: <Models data={data} onNavigate={navigate} csrf={csrf} onRefresh={load} />,
    keys: <Keys data={data} csrf={csrf} onRefresh={load} onAdd={() => setDialog('key')} />,
    test: <TestPanel data={data} csrf={csrf} />,
    routes: (
      <Routes
        data={data}
        csrf={csrf}
        onRefresh={load}
        onAdd={() => {
          setEditingRoute(null)
          setDialog('route')
        }}
        onEdit={(route) => {
          setEditingRoute(route)
          setDialog('route')
        }}
      />
    ),
    logs: <Logs csrf={csrf} />,
    automation: <Automation data={data} csrf={csrf} onRefresh={load} />,
    probes: <ProbePlansPage data={data} request={request} csrf={csrf} DataTable={DataTable} />,
    history: <ConfigHistoryPage request={request} csrf={csrf} DataTable={DataTable} Modal={Modal} onRefresh={load} />,
    backup: <Backup csrf={csrf} />,
    usage: <Usage settings={consoleSettings} refreshNonce={refreshNonce} />,
    releases: <ReleasesPage request={request} csrf={csrf} />,
    settings: (
      <>
        <SettingsPage
          settings={consoleSettings}
          theme={theme}
          onThemeChange={setTheme}
          onSave={saveConsoleSettings}
          onNavigate={navigate}
        />
        <ProviderSettings request={request} csrf={csrf} />
        <ProxySettingsPanel request={request} csrf={csrf} />
      </>
    ),
  }
  const content = pages[page]

  return (
    <div className="app-shell">
      {drawer && <button className="drawer-backdrop" aria-label="关闭导航" onClick={() => setDrawer(false)} />}
      <aside
        ref={drawerRef}
        className={`sidebar glass-panel ${drawer ? 'open' : ''}`}
        inert={mobile && !drawer ? true : undefined}
        aria-hidden={mobile && !drawer ? true : undefined}
      >
        {mobile && drawer && <DrawerFocus node={drawerRef} onClose={() => setDrawer(false)} />}
        <div className="sidebar-top">
          <a className="wordmark" href="/admin/">
            <img src="/admin/assets/brand.png" width="30" height="30" alt="" />
            Unified
          </a>
          <button className="mobile-close" aria-label="关闭导航" onClick={() => setDrawer(false)}>
            <X size={18} />
          </button>
        </div>
        <div className="environment">
          <span>
            <i />
            服务在线
          </span>
          <small>本地网关</small>
        </div>
        <nav>
          {navGroups.map((group) => (
            <div className="nav-group" key={group.label}>
              <span>{group.label}</span>
              {group.items.map(([id, Icon]) => (
                <button key={id} className={page === id ? 'active' : ''} aria-current={page === id ? 'page' : undefined} onClick={() => navigate(id)}>
                  <Icon size={17} />
                  {pageMeta[id][0]}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button className={page === 'settings' ? 'active' : ''} aria-current={page === 'settings' ? 'page' : undefined} onClick={() => navigate('settings')}>
            <Settings2 size={17} />
            设置
          </button>
          <button onClick={logout}>
            <LogOut size={17} />
            退出登录
          </button>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <button className="menu-button" aria-label="打开导航" onClick={() => setDrawer(true)}>
            <Menu size={19} />
          </button>
          <div>
            <span>Unified / {title}</span>
          </div>
          <div className="top-actions">
            {themeControl}
            <button className="icon-action" onClick={load} aria-label="刷新">
              <RefreshCw size={16} className={loading ? 'spin' : ''} />
            </button>
          </div>
        </header>
        <main className="page">
          <header className="page-heading">
            <div>
              <h1>{title}</h1>
              <p>{description}</p>
            </div>
          </header>
          {loadError && (
            <div className="result-banner error" role="alert">
              刷新失败：{loadError}
            </div>
          )}
          <PagePresence page={page}>{content}</PagePresence>
        </main>
      </div>
      {dialog === 'account' && (
        <AccountDialog key={dialog} csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved()} />
      )}
      {dialog === 'import' && (
        <BulkImportDialog request={request} csrf={csrf} Modal={Modal} onClose={() => setDialog(null)} onSaved={load} />
      )}
      {dialog === 'connection' && (
        <ConnectionDialog
          key={editingConnection?.id || dialog}
          csrf={csrf}
          initialConnection={editingConnection}
          onClose={() => setDialog(null)}
          onSaved={() => saved()}
        />
      )}
      {dialog === 'key' && <KeyDialog csrf={csrf} onClose={() => setDialog(null)} onSaved={() => saved(true)} />}
      {dialog === 'route' && (
        <RouteDialog
          key={editingRoute?.id || 'new'}
          data={data}
          csrf={csrf}
          initialRoute={editingRoute}
          onClose={() => setDialog(null)}
          onSaved={() => saved()}
        />
      )}
    </div>
  )
}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
