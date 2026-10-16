import React, { useEffect, useRef, useState } from 'react'
import { Clock, RefreshCw, Save } from 'lucide-react'
import './reliability.css'

export function CreditPolicyPanel({ data, request, csrf }) {
  const [unavailable, setUnavailable] = useState([])
  const [form, setForm] = useState(null),
    [rows, setRows] = useState([]),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [busy, setBusy] = useState(false)
  const dirty = useRef(false)
  function update(patch) {
    dirty.current = true
    setForm((value) => ({ ...value, ...patch }))
  }
  async function load() {
    const [config, result] = await Promise.all([
      request('unified/credit-policy'),
      request('unified/credit-policy/expiring'),
    ])
    if (!dirty.current) setForm(config.settings)
    setRows(result.items)
    setUnavailable(result.unavailable_providers || [])
  }
  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [data])
  async function save(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await request('unified/credit-policy', {
        method: 'PATCH',
        body: form,
        csrf,
      })
      dirty.current = false
      await load()
      setMessage('临期额度策略已保存')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <section className="content-card credit-policy-panel">
      <details>
        <summary>
          <span>
            <Clock size={17} />
            <strong>临期额度</strong>
          </span>
          <span className="muted">
            {rows.length
              ? `${rows.length} 个账号有即将到期的额度`
              : '查看到期额度与优先使用策略'}
          </span>
        </summary>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {form && (
          <div className="credit-policy-body">
            <form onSubmit={save}>
              <div className="credit-policy-controls">
                <label className="reliability-toggle">
                  <input
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) => update({ enabled: e.target.checked })}
                  />
                  优先使用临期额度
                </label>
                <label>
                  优先窗口（天）
                  <input
                    aria-label="额度临期天数"
                    type="number"
                    required
                    min="1"
                    max="90"
                    value={form.window_days}
                    onChange={(e) =>
                      update({ window_days: Number(e.target.value) })
                    }
                  />
                </label>
                <label className="reliability-toggle">
                  <input
                    type="checkbox"
                    checked={form.notify_expiring}
                    onChange={(e) =>
                      update({ notify_expiring: e.target.checked })
                    }
                  />
                  通过已配置的 Webhook 每日提醒
                </label>
              </div>
              <div className="head-actions">
                <button className="button primary" disabled={busy}>
                  <Save size={14} />
                  保存策略
                </button>
                <button
                  type="button"
                  className="button secondary"
                  disabled={busy}
                  onClick={() => load().catch((e) => setError(e.message))}
                >
                  <RefreshCw size={14} />
                  刷新列表
                </button>
                {message && (
                  <span className="success-text" role="status">
                    {message}
                  </span>
                )}
              </div>
            </form>
            <div className="expiring-accounts">
              {rows.map((row) => (
                <div key={row.provider + row.id}>
                  <div>
                    <strong>{row.name}</strong>
                    <small>
                      {row.provider === 'workbuddy'
                        ? 'WorkBuddy 国际'
                        : 'CodeBuddy 国内'}
                    </small>
                  </div>
                  <div>
                    <strong>
                      {row.expiring_credits.toLocaleString()} 积分
                    </strong>
                    <small>
                      {row.days_left < 1
                        ? '不足 1 天到期'
                        : `${row.days_left.toLocaleString()} 天后到期`}
                    </small>
                  </div>
                  <span
                    className={`probe-result ${row.preferred ? 'pass' : 'pending'}`}
                  >
                    {row.stale
                      ? '余额数据待刷新'
                      : !row.enabled
                        ? '已暂停'
                        : row.preferred
                          ? '优先使用'
                          : '未启用优先'}
                  </span>
                </div>
              ))}
            </div>
            <p className="field-help">
              只依据额度包有效期调度。余额超过 24
              小时未更新时暂停临期优先；点击账号行的「余额」按钮更新。
            </p>
            {unavailable.length > 0 && (
              <p className="form-error" role="status">
                部分平台的额度数据暂不可用，请稍后刷新。
              </p>
            )}
          </div>
        )}
      </details>
    </section>
  )
}
