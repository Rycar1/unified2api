import React, { useEffect, useState } from 'react'
import { Download, RefreshCw, Upload, Gift, WalletCards, ShieldCheck } from 'lucide-react'

const platform = (a) =>
  a.provider === 'qoder'
    ? `Qoder ${a.region === 'cn' ? '国内' : '国际'}`
    : { codebuddy: 'CodeBuddy 国内', workbuddy_intl: 'WorkBuddy 国际', trae: 'TRAE', monkeycode: 'MonkeyCode' }[
        a.provider
      ] || a.provider
const number = (n, suffix = '') =>
  n == null ? '—' : Number(n).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + suffix
const labels = {
  remaining: '余额',
  remain: '余额',
  total: '总额度',
  used: '已用额度',
  amount: '额度',
  credit: '积分',
  credits: '积分',
  earned_credit: '已领取积分',
  name: '名称',
  title: '名称',
  description: '说明',
  status: '状态',
  state: '状态',
  plan: '套餐',
  expireTime: '到期时间',
  expires_at: '到期时间',
  created_at: '发放时间',
  reason: '来源',
  type: '类型',
  count: '数量',
  level: '等级',
  energy: '能量',
  experience: '经验',
  message: '说明',
  msg: '说明',
  currency: '币种',
  id: '编号',
  uid: '账号编号',
  provider: '平台',
  account_id: '账号编号',
  startTime: '开始时间',
  endTime: '结束时间',
}
const hidden =
  /token|secret|password|authorization|cookie|headers|request|response|session|credential|key|^(provider|account_id|uid|realm|jump_url|task_code|domain|id)$/i
function Fields({ value, depth = 0 }) {
  if (value == null) return <span className="muted">未提供</span>
  if (depth > 5) return null
  if (Array.isArray(value))
    return value.length ? (
      <div className="task-list">
        {value.slice(0, 100).map((row, i) => (
          <div className="quota-entry" key={row.id || i}>
            <Fields value={row} depth={depth + 1} />
          </div>
        ))}
      </div>
    ) : (
      <p className="subtle-note">暂无记录</p>
    )
  if (typeof value !== 'object')
    return <span>{typeof value === 'boolean' ? (value ? '是' : '否') : String(value)}</span>
  const entries = Object.entries(value).filter(([key]) => !hidden.test(key))
  const simple = entries.filter(([, v]) => v == null || typeof v !== 'object')
  return (
    <>
      <dl className="details-pairs">
        {simple.map(([key, v]) => (
          <div key={key}>
            <dt>{labels[key] || key}</dt>
            <dd>
              <Fields value={v} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
      {entries
        .filter(([, v]) => v != null && typeof v === 'object')
        .map(([key, v]) => (
          <section key={key}>
            <h3 className="detail-label">
              {labels[key] ||
                {
                  packages: '额度套餐',
                  grants: '额度发放',
                  quota: '额度明细',
                  summary: '任务进度',
                  tasks: '任务列表',
                  items: '明细',
                  data: '明细',
                }[key] ||
                key}
            </h3>
            <Fields value={v} depth={depth + 1} />
          </section>
        ))}
    </>
  )
}
function Notice({ error, message }) {
  return error ? (
    <div className="result-banner error" role="alert">
      {error}
    </div>
  ) : message ? (
    <div className="result-banner" role="status">
      {message}
    </div>
  ) : null
}
function Loading() {
  return (
    <div className="skeleton-lines" aria-label="正在加载">
      <i />
      <i />
      <i />
    </div>
  )
}
function useRemote(request, path) {
  const [state, set] = useState({ loading: true, value: null, error: '' })
  const load = async () => {
    set((s) => ({ ...s, loading: true, error: '' }))
    try {
      const value = await request(path)
      set({ value, loading: false, error: '' })
    } catch (e) {
      set({ value: null, loading: false, error: e.message })
    }
  }
  useEffect(() => {
    let live = true
    set({ loading: true, value: null, error: '' })
    request(path)
      .then((value) => live && set({ value, loading: false, error: '' }))
      .catch((e) => live && set({ value: null, loading: false, error: e.message }))
    return () => {
      live = false
    }
  }, [path, request])
  return { ...state, load }
}

export function QuotaDialog({ account, request, Modal, onClose }) {
  const state = useRemote(request, `unified/accounts/${account.provider}/${encodeURIComponent(account.id)}/details`)
  return (
    <Modal title="额度明细" subtitle={`${platform(account)} · ${account.name || account.uid}`} onClose={onClose}>
      <Notice error={state.error} />
      {state.loading ? <Loading /> : state.value && <Fields value={state.value} />}
      <button className="button secondary" disabled={state.loading} onClick={state.load}>
        <RefreshCw size={15} />
        重新查询
      </button>
    </Modal>
  )
}

export function TasksPage({ data, request, csrf, onRefresh }) {
  const accounts = (data?.accounts || []).filter((a) => ['qoder', 'codebuddy', 'workbuddy_intl'].includes(a.provider))
  const accountKey = (a) => `${a.provider}:${a.id}`
  const [selected, setSelected] = useState(accounts[0] ? accountKey(accounts[0]) : '')
  const account = accounts.find((a) => accountKey(a) === selected) || accounts[0]
  const [tab, setTab] = useState('tasks'),
    [busy, setBusy] = useState(''),
    [message, setMessage] = useState(''),
    [error, setError] = useState('')
  const path =
    tab === 'history'
      ? 'unified/tasks/history'
      : tab === 'grants'
        ? 'unified/credits/grants'
        : account
          ? `unified/tasks/${account.provider}/${encodeURIComponent(account.id)}`
          : 'unified/tasks/history'
  const state = useRemote(request, path)
  useEffect(() => {
    setMessage('')
    setError('')
  }, [selected, tab])
  async function run(action) {
    setBusy(action)
    setError('')
    setMessage('')
    try {
      const r = await request(`unified/tasks/${account.provider}/${encodeURIComponent(account.id)}/${action}`, {
        method: 'POST',
        body: {},
        csrf,
      })
      if (r.ok === false) throw new Error(r.message || '任务执行失败')
      setMessage(r.message || '操作已完成')
      await state.load()
      await onRefresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  const actions =
    account?.provider === 'qoder'
      ? [
          ['checkin', '每日签到'],
          ['pro_claim', 'Pro 福利'],
          ['benefits', '领取可用福利'],
        ]
      : account?.provider === 'codebuddy'
        ? [
            ['checkin', '每日签到'],
            ['growth', '成长任务'],
            ['streak', '连续签到奖励'],
            ['travel', '猫猫旅行'],
          ]
        : [['daily_activity', '完成每日网页活跃']]
  return (
    <>
      <div className="filter-tabs" role="group" aria-label="福利记录分类">
        {[
          ['tasks', '福利任务'],
          ['history', '执行记录'],
          ['grants', '额度发放'],
        ].map(([key, name]) => (
          <button
            aria-pressed={key === tab}
            className={key === tab ? 'active' : ''}
            onClick={() => setTab(key)}
            key={key}
          >
            {name}
          </button>
        ))}
      </div>
      <div className={tab === 'tasks' ? 'extension-layout' : 'extension-single'}>
        {tab === 'tasks' && (
          <section className="content-card extension-accounts">
            {accounts.map((a) => (
              <button
                className={account && accountKey(a) === accountKey(account) ? 'active' : ''}
                onClick={() => setSelected(accountKey(a))}
                key={`${a.provider}-${a.id}`}
              >
                <strong>{a.name || a.nickname || a.uid}</strong>
                <small>{platform(a)}</small>
              </button>
            ))}
            {!accounts.length && <p className="subtle-note">先添加 CodeBuddy、WorkBuddy 或 Qoder 账号</p>}
          </section>
        )}
        <section className="content-card">
          <div className="section-head">
            <div>
              <span className="section-label">{tab === 'tasks' ? platform(account || {}) : '记录'}</span>
              <h2>{tab === 'tasks' ? '账号福利' : tab === 'history' ? '执行记录' : '额度发放明细'}</h2>
            </div>
            <button className="icon-action" aria-label="刷新福利记录" onClick={state.load} disabled={state.loading}>
              <RefreshCw size={16} />
            </button>
          </div>
          <Notice error={error || state.error} message={message} />
          {tab === 'tasks' && account && (
            <div className="head-actions task-actions">
              {actions.map(([action, name]) => (
                <button
                  className="button secondary"
                  disabled={!!busy || account.enabled === false}
                  onClick={() => run(action)}
                  key={action}
                >
                  <Gift size={14} />
                  {busy === action ? '执行中…' : name}
                </button>
              ))}
            </div>
          )}
          {state.loading ? <Loading /> : state.value && <Fields value={state.value} />}
        </section>
      </div>
    </>
  )
}

export function ProviderSettings({ request, csrf }) {
  const state = useRemote(request, 'unified/provider-settings/workbuddy')
  const [form, setForm] = useState({}),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState(''),
    [error, setError] = useState('')
  useEffect(() => {
    if (state.value)
      setForm(
        Object.fromEntries(
          [
            'reserve_credits',
            'daily_token_limit',
            'daily_credit_limit',
            'model_daily_token_limit',
            'local_web_tools',
            'pricing_enabled',
            'remaining_priority_enabled',
          ].map((k) => [k, state.value[k] ?? (k.includes('enabled') || k === 'local_web_tools' ? false : 0)]),
        ),
      )
  }, [state.value])
  async function save(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await request('unified/provider-settings/workbuddy', { method: 'PATCH', body: form, csrf })
      setMessage('账号保护设置已保存')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="content-card" onSubmit={save}>
      <span className="section-label">WorkBuddy 国际</span>
      <h2>账号保护与联网</h2>
      <p className="subtle-note">按每个账号单独累计。填 0 关闭对应限制；国内 CodeBuddy 账号池仍独立管理。</p>
      <Notice error={error || state.error} message={message} />
      {state.loading ? (
        <Loading />
      ) : (
        <>
          <div className="compact-grid">
            {[
              ['reserve_credits', '保留积分'],
              ['daily_token_limit', '每日 Token 上限'],
              ['daily_credit_limit', '每日积分上限'],
              ['model_daily_token_limit', '每模型每日 Token 上限'],
            ].map(([key, name]) => (
              <div key={key}>
                <label htmlFor={`guard-${key}`}>{name}</label>
                <input
                  id={`guard-${key}`}
                  type="number"
                  min="0"
                  max="1000000000000"
                  value={form[key] ?? 0}
                  onChange={(e) => setForm({ ...form, [key]: Number(e.target.value) })}
                />
              </div>
            ))}
          </div>
          {[
            ['local_web_tools', '执行模型的网页搜索与抓取工具'],
            ['pricing_enabled', '显示等价费用估算'],
            ['remaining_priority_enabled', '优先选择余额较多的账号'],
          ].map(([key, name]) => (
            <label className="switch-line" key={key}>
              <input
                type="checkbox"
                checked={!!form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
              />
              {name}
            </label>
          ))}
          <button className="button primary" disabled={busy || !!state.error}>
            <ShieldCheck size={15} />
            {busy ? '保存中…' : '保存设置'}
          </button>
        </>
      )}
    </form>
  )
}

export function PerformancePage({ request, DataTable }) {
  const [days, setDays] = useState(30)
  const [metric, setMetric] = useState('estimated_cost_cny')
  const state = useRemote(request, `unified/performance?days=${days}`),
    r = state.value || {}
  const series = r.series || [],
    peak = Math.max(1, ...series.map((row) => Number(row[metric]) || 0))
  const metrics = [
    ['等价费用', r.estimated_cost_cny == null ? '—' : `¥${number(r.estimated_cost_cny)}`],
    ['缓存命中', number(r.cache_hit_percent, '%')],
    ['平均首 Token', number(r.average_ttft_ms, ' ms')],
    ['平均生成速度', number(r.tokens_per_second, ' token/s')],
  ]
  return (
    <>
      <div className="toolbar">
        <div className="filter-tabs">
          {[
            [1, '近 24 小时'],
            [7, '近 7 天'],
            [30, '近 30 天'],
            [0, '累计'],
          ].map(([id, name]) => (
            <button
              className={days === id ? 'active' : ''}
              aria-pressed={days === id}
              key={id}
              onClick={() => setDays(id)}
            >
              {name}
            </button>
          ))}
        </div>
        <button className="icon-action" aria-label="刷新性能统计" onClick={state.load}>
          <RefreshCw size={16} />
        </button>
      </div>
      <Notice error={state.error} />
      <div className="metric-grid">
        {metrics.map(([name, value]) => (
          <article key={name}>
            <small>{name}</small>
            <strong>{state.loading ? '…' : value}</strong>
          </article>
        ))}
      </div>
      <section className="content-card">
        <div className="section-head">
          <h2>近期每日走势</h2>
          <select aria-label="走势指标" value={metric} onChange={(e) => setMetric(e.target.value)}>
            {[
              ['estimated_cost_cny', '等价费用'],
              ['cache_hit_percent', '缓存命中率'],
              ['tokens_per_second', '生成速度'],
              ['average_ttft_ms', '首 Token 耗时'],
            ].map(([key, name]) => (
              <option value={key} key={key}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {series.length ? (
          <div className="performance-chart" role="img" aria-label="每日性能与费用走势">
            {series.map((row, index) => (
              <div key={row.day} tabIndex="0" title={`${row.day} · ${number(row[metric])}`}>
                <i
                  style={{ height: row[metric] == null ? '0' : `${Math.max(1, (Number(row[metric]) / peak) * 100)}%` }}
                />
                <span className="chart-tooltip">
                  {row.day}
                  <br />
                  {number(row[metric])}
                </span>
                <small>
                  {series.length <= 7 || index % Math.ceil(series.length / 7) === 0 ? row.day.slice(5) : ''}
                </small>
              </div>
            ))}
          </div>
        ) : (
          <p className="subtle-note">暂无走势数据；有记录后显示实际值。</p>
        )}
      </section>
      <section className="content-card">
        <div className="section-head">
          <h2>模型性能</h2>
          <span className="muted">{number(r.requests)} 次请求</span>
        </div>
        <p className="subtle-note">
          费用按公开模型价格估算，与平台积分独立。缓存数据覆盖 {number(r.known_cache_requests)} 次请求，价格覆盖{' '}
          {number(r.priced_requests)} 次；历史未知数据留空。
        </p>
        <DataTable
          columns={['模型', '请求', '成功率', '缓存命中', '生成速度', '等价费用']}
          empty={!state.loading && !r.models?.length && '暂无统计数据'}
        >
          {(r.models || []).map((row) => (
            <tr key={row.model}>
              <td>
                <code>{row.model}</code>
              </td>
              <td>{number(row.requests)}</td>
              <td>{number(row.success_rate, '%')}</td>
              <td>{number(row.cache_hit_percent, '%')}</td>
              <td>{number(row.tokens_per_second, ' /s')}</td>
              <td>{row.estimated_cost_cny == null ? '—' : `¥${number(row.estimated_cost_cny)}`}</td>
            </tr>
          ))}
        </DataTable>
      </section>
      <section className="content-card">
        <h2>平台积分消耗</h2>
        <dl className="details-pairs">
          {(r.providers || [])
            .filter((p) => p.provider)
            .map((p) => (
              <div key={p.provider}>
                <dt>{platform({ provider: p.provider })}</dt>
                <dd>
                  {number(p.credits)} <small>· {number(p.known_credit_requests)} 次提供计费数据</small>
                </dd>
              </div>
            ))}
        </dl>
      </section>
    </>
  )
}

export function BulkImportDialog({ request, csrf, Modal, onClose, onSaved }) {
  const [provider, setProvider] = useState('codebuddy'),
    [credentials, setCredentials] = useState(''),
    [overwrite, setOverwrite] = useState(false),
    [preview, setPreview] = useState(null),
    [result, setResult] = useState(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function run(apply = false) {
    setBusy(true)
    setError('')
    try {
      const r = await request('unified/accounts/import', {
        method: 'POST',
        csrf,
        body: { provider, credentials, overwrite, preview: !apply },
      })
      if (apply) {
        setResult(r)
        await onSaved()
      } else {
        setPreview(r)
        setResult(null)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  function changed(fn) {
    fn()
    setPreview(null)
    setResult(null)
    setError('')
  }
  return (
    <Modal title="批量导入账号" subtitle="先预览，再导入" onClose={onClose}>
      <label htmlFor="bulk-provider">平台</label>
      <select id="bulk-provider" value={provider} onChange={(e) => changed(() => setProvider(e.target.value))}>
        {[
          ['trae', 'TRAE'],
          ['codebuddy', 'CodeBuddy 国内'],
          ['workbuddy_intl', 'WorkBuddy 国际'],
          ['monkeycode', 'MonkeyCode'],
          ['qoder', 'Qoder 国际'],
          ['qoder-cn', 'Qoder 国内'],
        ].map(([key, name]) => (
          <option key={key} value={key}>
            {name}
          </option>
        ))}
      </select>
      <label htmlFor="bulk-credentials">凭据 JSON 数组</label>
      <textarea
        id="bulk-credentials"
        rows="8"
        value={credentials}
        onChange={(e) => changed(() => setCredentials(e.target.value))}
        placeholder="一次最多 100 个账号"
        autoComplete="off"
        spellCheck="false"
      />
      <label className="switch-line">
        <input type="checkbox" checked={overwrite} onChange={(e) => changed(() => setOverwrite(e.target.checked))} />
        覆盖已有账号凭据
      </label>
      <Notice error={error} />
      {preview && (
        <div className="import-preview">
          {preview.rows.map((row) => (
            <div key={row.index}>
              <strong>{row.name}</strong>
              <span className="muted">
                {!row.valid ? '格式无效' : row.exists ? (overwrite ? '更新已有账号' : '跳过已有账号') : '新增账号'}
              </span>
            </div>
          ))}
        </div>
      )}
      {result && (
        <>
          <Notice message={`导入 ${result.imported} 个，跳过 ${result.skipped} 个，失败 ${result.failed} 个`} />
          {result.results
            .filter((r) => !r.ok)
            .map((r) => (
              <p className="form-error" key={r.index}>
                {r.name}：{r.message}
              </p>
            ))}
        </>
      )}
      <div className="head-actions">
        <button className="button secondary" disabled={busy || !credentials.trim()} onClick={() => run()}>
          <Upload size={15} />
          预览
        </button>
        <button
          className="button primary"
          disabled={busy || !preview || preview.rows.some((r) => !r.valid)}
          onClick={() => run(true)}
        >
          {busy ? '处理中…' : '确认导入'}
        </button>
      </div>
    </Modal>
  )
}

export function ClientsPage({ data, request, csrf }) {
  const [client, setClient] = useState('codex'),
    [model, setModel] = useState(''),
    [key, setKey] = useState(''),
    [base, setBase] = useState(`${window.location.origin}/v1`),
    [search, setSearch] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const state = useRemote(request, 'unified/client-setup')
  const models = (data?.models || []).filter((m) => !search || m.id.toLowerCase().includes(search.toLowerCase()))
  async function download(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const response = await fetch('/admin/api/unified/client-setup/download', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        body: JSON.stringify({ client, model, api_key: key, base_url: base }),
      })
      if (!response.ok) {
        const r = await response.json()
        throw new Error(r.detail || '下载失败')
      }
      const url = URL.createObjectURL(await response.blob()),
        a = document.createElement('a')
      a.href = url
      a.download = 'unified-client-setup.zip'
      a.click()
      setTimeout(() => URL.revokeObjectURL(url), 30000)
      setMessage('配置包已下载，解压后运行 configure；恢复使用 restore。')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="content-card client-setup" onSubmit={download}>
      <div className="section-head">
        <div>
          <span className="section-label">本机配置助手</span>
          <h2>连接你的客户端</h2>
        </div>
      </div>
      <Notice error={error || state.error} message={message} />
      <div className="compact-grid">
        <div>
          <label htmlFor="client-type">客户端</label>
          <select id="client-type" value={client} onChange={(e) => setClient(e.target.value)}>
            {(state.value?.clients || []).map((c) => (
              <option value={c.id} key={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="client-base">API Base URL</label>
          <input id="client-base" type="url" required value={base} onChange={(e) => setBase(e.target.value)} />
        </div>
        <div>
          <label htmlFor="client-search">筛选模型</label>
          <input
            id="client-search"
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="输入模型名称或前缀"
          />
        </div>
        <div>
          <label htmlFor="client-model">模型</label>
          <select id="client-model" required value={model} onChange={(e) => setModel(e.target.value)}>
            <option value="">选择模型</option>
            {model && !models.some((m) => m.id === model) && <option value={model}>{model}</option>}
            {models.map((m) => (
              <option value={m.id} key={m.id}>
                {m.id}
              </option>
            ))}
          </select>
        </div>
      </div>
      <label htmlFor="client-key">客户端 API 密钥</label>
      <input
        id="client-key"
        type="password"
        autoComplete="off"
        required
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder="填入在 API 密钥页面创建的密钥"
      />
      <p className="subtle-note">
        配置包包含此密钥，请自行保管。原客户端配置会自动备份，可一键恢复。
        {state.value?.windows_standalone ? 'Windows 无需安装 Python。' : '需要 Python 3。'}Codex 使用 Responses
        接口，远程压缩取决于所选模型。
      </p>
      <button className="button primary" disabled={busy || state.loading}>
        <Download size={15} />
        {busy ? '生成中…' : '下载配置包'}
      </button>
    </form>
  )
}
