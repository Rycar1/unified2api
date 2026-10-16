import React, { useEffect, useState } from 'react'
import { Activity, KeyRound, Plus, RefreshCw, Save, Trash2, WalletCards } from 'lucide-react'
import './operations.css'

const timeText = (value) =>
  value ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '尚未执行'
const moneyText = (balance) =>
  balance
    ? `${Number(balance.remaining).toLocaleString('zh-CN', { maximumFractionDigits: 4 })} ${balance.unit}`
    : '未查询'
function Notice({ error, message }) {
  return error ? (
    <p className="form-error" role="alert">
      {error}
    </p>
  ) : message ? (
    <p className="result-banner" role="status">
      {message}
    </p>
  ) : null
}

export function NetworkPanel({ request, csrf }) {
  const [form, setForm] = useState(null),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [busy, setBusy] = useState(false)
  useEffect(() => {
    let live = true
    request('unified/network')
      .then((r) => live && setForm(r.settings))
      .catch((e) => live && setError(e.message))
    return () => {
      live = false
    }
  }, [request])
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const r = await request('unified/network', { method: 'PATCH', body: form, csrf })
      setForm(r.settings)
      setMessage('连接设置已保存，下次请求生效')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <form className="content-card operations-form" onSubmit={save}>
      <span className="section-label">连接与流式响应</span>
      <h2>等待多久，由你决定</h2>
      <p className="muted">
        首字等待包含模型思考时间；流式空闲指连续收不到数据的时长。单次请求已经输出内容后不会自动重放。
      </p>
      {form ? (
        <>
          <div className="field-grid">
            <label htmlFor="first-token-timeout">
              首个有效响应等待（秒）
              <input
                id="first-token-timeout"
                type="number"
                min="5"
                max="3600"
                required
                value={form.first_token_seconds}
                onChange={(e) => setForm({ ...form, first_token_seconds: Number(e.target.value) })}
              />
            </label>
            <label htmlFor="stream-idle-timeout">
              流式空闲超时（秒）
              <input
                id="stream-idle-timeout"
                type="number"
                min="5"
                max="3600"
                required
                value={form.idle_seconds}
                onChange={(e) => setForm({ ...form, idle_seconds: Number(e.target.value) })}
              />
            </label>
          </div>
          <p className="field-help">
            首字超时会返回 504；输出后的空闲超时会返回明确的流式错误。路由的重试次数在对应路由中设置。
          </p>
          <button className="button primary" disabled={busy}>
            <Save size={14} />
            {busy ? '保存中…' : '保存连接设置'}
          </button>
        </>
      ) : (
        !error && <p role="status">正在加载连接设置…</p>
      )}
      <Notice error={error} message={message} />
    </form>
  )
}

export function RoutePlanDialog({ route, request, Modal, onClose }) {
  const [protocol, setProtocol] = useState('chat'),
    [plan, setPlan] = useState(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  async function load() {
    setBusy(true)
    setError('')
    try {
      setPlan(await request(`unified/routes/${encodeURIComponent(route.id)}/plan?protocol=${protocol}`))
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  useEffect(() => {
    load()
  }, [route.id, protocol])
  return (
    <Modal title="路由预览" subtitle={`route/${route.id}`} onClose={onClose}>
      <div className="operations-toolbar">
        <label htmlFor="plan-protocol">
          接口协议
          <select id="plan-protocol" value={protocol} onChange={(e) => setProtocol(e.target.value)}>
            <option value="chat">Chat Completions</option>
            <option value="responses">Responses</option>
            <option value="messages">Messages</option>
            <option value="compact">原生压缩</option>
          </select>
        </label>
        <button className="button secondary" onClick={load} disabled={busy}>
          <RefreshCw size={14} className={busy ? 'spin' : ''} />
          刷新预览
        </button>
      </div>
      <p className="field-help">
        根据当前状态预览，不发送模型请求，也不改变轮询顺序。账号列表表示候选状态，实际分配以请求执行时为准。
      </p>
      <Notice error={error} />
      {plan && (
        <>
          <div className="operations-highlight">
            <Activity size={20} />
            <div>
              <small>下次优先尝试</small>
              <strong>{plan.selected || '暂无可用目标'}</strong>
            </div>
          </div>
          <ol className="route-plan-list">
            {[...plan.items]
              .sort((a, b) => (a.position || 999) - (b.position || 999))
              .map((item) => (
                <li key={item.model} className={item.model === plan.selected ? 'is-selected' : ''}>
                  <div className="route-plan-heading">
                    <span className="plan-position">{item.position || '—'}</span>
                    <code>{item.model}</code>
                    <span className="provider-badge">{item.active} 个在途</span>
                  </div>
                  <p className="muted">
                    {item.reason}
                    {item.cooldown_until * 1000 > Date.now()
                      ? ` · 恢复于 ${timeText(item.cooldown_until)}`
                      : ''}
                    {item.last_status ? ` · 上次 HTTP ${item.last_status}` : ''}
                  </p>
                  {!!item.accounts?.length && (
                    <div className="candidate-list">
                      {item.accounts.map((account) => (
                        <div key={account.id}>
                          <span className={`candidate-dot ${account.available ? 'ready' : ''}`} />
                          <strong>{account.name || account.id}</strong>
                          <small>
                            {account.available
                              ? '可参与分配'
                              : {
                                  paused: '已停用',
                                  exhausted: '余额不足',
                                  cooling: '冷却中',
                                  busy: '并发已满',
                                  invalid: '凭据异常',
                                  expired: '已过期',
                                }[account.status] || '当前不可用'}
                          </small>
                        </div>
                      ))}
                    </div>
                  )}
                </li>
              ))}
          </ol>
        </>
      )}
    </Modal>
  )
}

export function ConnectionPoolDialog({ connection, request, csrf, Modal, onClose, onSaved }) {
  const [value, setValue] = useState(null),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [busy, setBusy] = useState('')
  const [form, setForm] = useState(null),
    [scheme, setScheme] = useState(connection.balance_scheme || 'none')
  const path = `unified/connections/${encodeURIComponent(connection.id)}/accounts`
  async function load() {
    const r = await request(path)
    setValue(r)
  }
  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [connection.id])
  async function perform(id, action, body) {
    setBusy(id || action)
    setError('')
    setMessage('')
    try {
      const r = await request(
        `${path}${id ? `/${encodeURIComponent(id)}` : ''}${action === 'balance' ? '/balance' : ''}`,
        { method: action === 'delete' ? 'DELETE' : action === 'edit' ? 'PATCH' : 'POST', body, csrf },
      )
      setMessage(action === 'balance' ? `余额已更新：${moneyText(r)}` : '账号池已更新')
      if (action === 'add' || action === 'edit') setForm(null)
      await load()
      await onSaved()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  async function saveScheme(event) {
    event.preventDefault()
    setBusy('scheme')
    setError('')
    try {
      await request(`unified/connections/${encodeURIComponent(connection.id)}`, {
        method: 'PATCH',
        body: { balance_scheme: scheme },
        csrf,
      })
      setMessage('余额查询方案已保存')
      await load()
      await onSaved()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  const accountNames = {
    paused: '已停用',
    exhausted: '余额不足',
    busy: '并发已满',
    cooling: '冷却中',
    ready: '可用',
  }
  return (
    <Modal title="服务账号池" subtitle={`${connection.name} · ${connection.id}/`} onClose={onClose}>
      <Notice error={error} message={message} />
      <div className="operations-toolbar">
        <p className="muted">多个 Key 轮流使用，失败账号自动冷却 60 秒。</p>
        <button
          className="button primary"
          disabled={!!busy}
          onClick={() =>
            setForm({
              name: '',
              key: '',
              enabled: true,
              max_concurrency: 2,
              balance_key: '',
              balance_user_id: '',
            })
          }
        >
          <Plus size={14} />
          添加账号
        </button>
      </div>
      <div className="pool-account-list">
        {value?.accounts.map((account) => (
          <article key={account.id}>
            <div className="pool-account-top">
              <span className="pool-key-icon">
                <KeyRound size={18} />
              </span>
              <div>
                <strong>{account.name}</strong>
                <small>
                  {accountNames[account.status]} · {account.active} 个在途
                  {account.max_concurrency ? ` / 上限 ${account.max_concurrency}` : ' / 不限并发'}
                </small>
              </div>
              <strong className="pool-balance">{moneyText(account.balance)}</strong>
            </div>
            {account.balance && (
              <small className="muted">余额更新于 {timeText(account.balance.updated)}</small>
            )}
            {account.last_error && <p className="form-error">{account.last_error}</p>}
            <div className="inline-actions">
              <button
                className="button secondary"
                disabled={!!busy || value.balance_scheme === 'none'}
                onClick={() => perform(account.id, 'balance', {})}
              >
                <WalletCards size={13} />
                {busy === account.id ? '处理中…' : '查询余额'}
              </button>
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => setForm({ ...account, key: '', balance_key: '' })}
              >
                编辑
              </button>
              <button
                className="button secondary"
                disabled={!!busy}
                onClick={() => perform(account.id, 'edit', { enabled: !account.enabled })}
              >
                {account.enabled ? '停用' : '启用'}
              </button>
              <button
                className="button secondary danger-text"
                disabled={!!busy || value.accounts.length < 2}
                onClick={() => {
                  if (window.confirm(`删除账号“${account.name}”？`)) perform(account.id, 'delete')
                }}
              >
                <Trash2 size={13} />
                删除
              </button>
            </div>
          </article>
        ))}
      </div>
      {form && (
        <form
          className="operations-inset operations-form"
          onSubmit={(e) => {
            e.preventDefault()
            const { id, name, key, enabled, max_concurrency, balance_key, balance_user_id } = form
            perform(id, id ? 'edit' : 'add', {
              name,
              key,
              enabled,
              max_concurrency,
              balance_key,
              balance_user_id,
            })
          }}
        >
          <h3>{form.id ? '编辑账号' : '添加账号'}</h3>
          <label htmlFor="pool-name">
            账号名称
            <input
              id="pool-name"
              required
              maxLength="60"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label htmlFor="pool-key">
            API Key
            <input
              id="pool-key"
              type="password"
              autoComplete="new-password"
              required={!form.id}
              placeholder={form.id ? '留空保持原 Key' : '服务商提供的 Key'}
              value={form.key}
              onChange={(e) => setForm({ ...form, key: e.target.value })}
            />
          </label>
          <label htmlFor="pool-concurrency">
            账号并发上限（0 表示不限）
            <input
              id="pool-concurrency"
              type="number"
              min="0"
              max="100"
              required
              value={form.max_concurrency}
              onChange={(e) => setForm({ ...form, max_concurrency: Number(e.target.value) })}
            />
          </label>
          {scheme === 'newapi' && (
            <>
              <label htmlFor="pool-balance-key">
                余额查询 Access Token
                <input
                  id="pool-balance-key"
                  type="password"
                  autoComplete="new-password"
                  placeholder={form.has_balance_key ? '留空保持原查询令牌' : 'NewAPI 控制台访问令牌'}
                  value={form.balance_key}
                  onChange={(e) => setForm({ ...form, balance_key: e.target.value })}
                />
              </label>
              <label htmlFor="pool-user-id">
                NewAPI 用户 ID
                <input
                  id="pool-user-id"
                  value={form.balance_user_id}
                  onChange={(e) => setForm({ ...form, balance_user_id: e.target.value })}
                />
              </label>
            </>
          )}
          <div className="inline-actions">
            <button className="button primary" disabled={!!busy}>
              <Save size={14} />
              保存账号
            </button>
            <button type="button" className="button secondary" onClick={() => setForm(null)}>
              取消
            </button>
          </div>
        </form>
      )}
      <form className="operations-inset operations-form" onSubmit={saveScheme}>
        <label htmlFor="balance-scheme">
          余额查询方案
          <select id="balance-scheme" value={scheme} onChange={(e) => setScheme(e.target.value)}>
            <option value="none">暂不查询</option>
            <option value="deepseek">DeepSeek</option>
            <option value="newapi">NewAPI</option>
            <option value="sub2api">Sub2API</option>
          </select>
        </label>
        <p className="field-help">
          DeepSeek 和 Sub2API 使用账号自己的 Key。NewAPI 需要编辑账号填写独立的查询令牌；原始 quota
          按上游单位展示。
        </p>
        <button className="button secondary" disabled={!!busy}>
          <Save size={14} />
          保存查询方案
        </button>
      </form>
    </Modal>
  )
}
