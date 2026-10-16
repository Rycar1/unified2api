import React, { useEffect, useState } from 'react'
import { CalendarClock, Clock, Play, Plus, RefreshCw, Save, Trash2 } from 'lucide-react'
import './operations.css'

const platforms = [
  ['trae', 'TRAE'],
  ['codebuddy', 'CodeBuddy 国内'],
  ['workbuddy_intl', 'WorkBuddy 国际'],
  ['monkeycode', 'MonkeyCode'],
  ['qoder-cn', 'Qoder 国内'],
  ['qoder', 'Qoder 国际'],
]
const dateText = (value) =>
  value ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '未安排'
const jobText = { checkin: '签到', refresh: '刷新状态和余额', balance: '查询余额', benefits: '领取福利' }

export function AutomationCenter({ data, request, csrf, onRefresh }) {
  const [form, setForm] = useState(null),
    [status, setStatus] = useState(null),
    [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [message, setMessage] = useState(''),
    [dirty, setDirty] = useState(false)
  async function load() {
    const r = await request('unified/automation')
    setForm(r.settings)
    setStatus(r)
    setDirty(false)
  }
  useEffect(() => {
    if (!dirty) load().catch((e) => setError(e.message))
  }, [data])
  const accounts = [
    ...(data?.accounts || []).map((a) => ({
      ...a,
      provider: a.provider === 'qoder' && a.region === 'cn' ? 'qoder-cn' : a.provider,
    })),
    ...(data?.connections || [])
      .filter((c) => c.balance_scheme && c.balance_scheme !== 'none')
      .flatMap((c) => c.accounts.map((a) => ({ ...a, provider: c.id, name: `${c.name} / ${a.name}` }))),
  ]
  const accountLabel = (row) =>
    accounts.find((a) => a.provider === row.provider && a.id === row.account_id)?.name ||
    `${row.provider} / ${row.account_id}`
  const update = (key, value) => {
    setDirty(true)
    setForm((old) => ({ ...old, [key]: value }))
  }
  const plans = form?.schedules ?? [
    {
      id: 'default',
      label: '默认签到',
      time: form?.checkin_time || '09:00',
      providers: platforms.map((p) => p[0]),
      enabled: true,
    },
  ]
  const refresh = form?.account_refresh || []
  const changePlan = (index, patch) =>
    update(
      'schedules',
      plans.map((p, i) => (i === index ? { ...p, ...patch } : p)),
    )
  const changeRefresh = (index, patch) =>
    update(
      'account_refresh',
      refresh.map((p, i) => (i === index ? { ...p, ...patch } : p)),
    )
  async function save(event) {
    event.preventDefault()
    setBusy('save')
    setError('')
    setMessage('')
    try {
      await request('unified/automation', { method: 'PATCH', body: form, csrf })
      await load()
      await onRefresh()
      setMessage('自动任务设置已保存')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  async function run(action, id) {
    setBusy(id || action)
    setError('')
    setMessage('')
    try {
      const r = await request(
        id ? `unified/automation/plans/${encodeURIComponent(id)}/run` : `unified/automation/run/${action}`,
        { method: 'POST', body: {}, csrf },
      )
      const summary = r.result?.summary || {}
      setMessage(`执行完成：成功 ${summary.succeeded || 0}，失败 ${summary.failed || 0}`)
      await load()
      await onRefresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  if (!form)
    return (
      <section className="content-card">
        {error ? (
          <p role="alert" className="form-error">
            {error}
          </p>
        ) : (
          <p role="status">正在加载自动任务…</p>
        )}
      </section>
    )
  return (
    <form className="stack automation-center" onSubmit={save}>
      <div className="operations-savebar">
        <div>
          <strong>{dirty ? '有未保存的更改' : '自动任务'}</strong>
          <small>北京时间 UTC+8 · 每 30 秒检查 · 失败签到最多尝试 3 次</small>
        </div>
        <button className="button primary" disabled={!!busy || !dirty}>
          <Save size={14} />
          {busy === 'save' ? '保存中…' : '保存全部设置'}
        </button>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="result-banner" role="status">
          {message}
        </p>
      )}
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">每日计划</span>
            <h2>按平台安排签到</h2>
          </div>
          <button
            type="button"
            className="button secondary"
            disabled={plans.length >= 10 || !!busy}
            onClick={() =>
              update('schedules', [
                ...plans,
                {
                  id: crypto.randomUUID(),
                  label: '',
                  time: '09:00',
                  providers: ['codebuddy'],
                  enabled: true,
                },
              ])
            }
          >
            <Plus size={14} />
            添加计划
          </button>
        </div>
        <label className="switch-row">
          <span>
            <strong>启用自动签到</strong>
            <small>控制下方全部签到计划，WorkBuddy 国际执行每日网页活跃</small>
          </span>
          <input
            type="checkbox"
            checked={!!form.auto_checkin}
            onChange={(e) => update('auto_checkin', e.target.checked)}
          />
        </label>
        <div className="schedule-list">
          {plans.map((plan, index) => {
            const state = status?.schedules.find((s) => s.id === plan.id)
            return (
              <article key={plan.id} className="schedule-item">
                <div className="schedule-icon">
                  <CalendarClock size={20} />
                </div>
                <div className="schedule-body">
                  <div className="schedule-fields">
                    <label htmlFor={`plan-name-${plan.id}`}>
                      计划名称
                      <input
                        id={`plan-name-${plan.id}`}
                        maxLength="60"
                        placeholder="例如：国内平台早间签到"
                        value={plan.label}
                        onChange={(e) => changePlan(index, { label: e.target.value })}
                      />
                    </label>
                    <label htmlFor={`plan-time-${plan.id}`}>
                      执行时间
                      <input
                        id={`plan-time-${plan.id}`}
                        type="time"
                        required
                        value={plan.time}
                        onChange={(e) => changePlan(index, { time: e.target.value })}
                      />
                    </label>
                  </div>
                  <fieldset className="schedule-platforms">
                    <legend>执行平台</legend>
                    {platforms.map(([id, label]) => (
                      <label key={id}>
                        <input
                          type="checkbox"
                          checked={plan.providers.includes(id)}
                          onChange={(e) =>
                            changePlan(index, {
                              providers: e.target.checked
                                ? [...plan.providers, id]
                                : plan.providers.filter((p) => p !== id),
                            })
                          }
                        />
                        {label}
                      </label>
                    ))}
                  </fieldset>
                  <p className="schedule-status">
                    <Clock size={12} />
                    {!plan.enabled || !form.auto_checkin
                      ? '自动执行已关闭'
                      : `下次执行：${dateText(state?.next_run)}`}
                    {state?.last_attempt
                      ? ` · 上次${state.last_ok ? '成功' : '失败'} ${dateText(state.last_attempt)}`
                      : ''}
                  </p>
                  {state?.last_error && <p className="form-error">{state.last_error}</p>}
                </div>
                <div className="schedule-actions">
                  <label className="switch-row">
                    <span>启用</span>
                    <input
                      type="checkbox"
                      checked={plan.enabled}
                      onChange={(e) => changePlan(index, { enabled: e.target.checked })}
                    />
                  </label>
                  <button
                    type="button"
                    className="button secondary"
                    disabled={!!busy || dirty}
                    onClick={() => run('checkin', plan.id)}
                  >
                    <Play size={13} />
                    立即执行
                  </button>
                  <button
                    type="button"
                    className="button secondary danger-text"
                    aria-label={`删除计划 ${plan.label || plan.time}`}
                    onClick={() =>
                      update(
                        'schedules',
                        plans.filter((_, i) => i !== index),
                      )
                    }
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </article>
            )
          })}
        </div>
        {!plans.length && <p className="empty-state">还没有签到计划，点击“添加计划”开始设置。</p>}
        <p className="field-help">
          修改后保存才会生效。执行时间已过且当天尚未执行的计划，会在服务启动后补执行。
        </p>
      </section>
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">余额同步</span>
            <h2>每个账号有自己的节奏</h2>
          </div>
          <button
            type="button"
            className="button secondary"
            disabled={!accounts.length || !!busy}
            onClick={() => {
              const account = accounts.find(
                (a) => !refresh.some((r) => r.provider === a.provider && r.account_id === a.id),
              )
              if (account)
                update('account_refresh', [
                  ...refresh,
                  { provider: account.provider, account_id: account.id, minutes: 30, enabled: true },
                ])
            }}
          >
            <Plus size={14} />
            添加账号规则
          </button>
        </div>
        <div className="schedule-list">
          {refresh.map((row, index) => {
            const selected = JSON.stringify({ provider: row.provider, account_id: row.account_id })
            const state = status?.account_refresh.find(
              (s) => s.key === `balance:${row.provider}:${row.account_id}`,
            )
            return (
              <div className="account-refresh-rule" key={index}>
                <label htmlFor={`refresh-account-${index}`}>
                  账号
                  <select
                    id={`refresh-account-${index}`}
                    value={selected}
                    onChange={(e) => changeRefresh(index, JSON.parse(e.target.value))}
                  >
                    <option value={selected}>{accountLabel(row)}</option>
                    {accounts
                      .filter((a) => !(a.provider === row.provider && a.id === row.account_id))
                      .map((a) => (
                        <option
                          key={`${a.provider}:${a.id}`}
                          value={JSON.stringify({ provider: a.provider, account_id: a.id })}
                        >
                          {a.name} · {a.provider}
                        </option>
                      ))}
                  </select>
                </label>
                <label htmlFor={`refresh-minutes-${index}`}>
                  间隔（分钟）
                  <input
                    id={`refresh-minutes-${index}`}
                    type="number"
                    min="5"
                    max="1440"
                    required
                    value={row.minutes}
                    onChange={(e) => changeRefresh(index, { minutes: Number(e.target.value) })}
                  />
                </label>
                <label className="switch-row">
                  <span>启用</span>
                  <input
                    type="checkbox"
                    checked={row.enabled}
                    onChange={(e) => changeRefresh(index, { enabled: e.target.checked })}
                  />
                </label>
                <button
                  type="button"
                  className="button secondary danger-text"
                  aria-label={`删除刷新规则 ${accountLabel(row)}`}
                  onClick={() =>
                    update(
                      'account_refresh',
                      refresh.filter((_, i) => i !== index),
                    )
                  }
                >
                  <Trash2 size={14} />
                </button>
                <p className="schedule-status">
                  {row.enabled ? `下次查询：${dateText(state?.next_run)}` : '已关闭'}
                  {state?.last_error ? ` · ${state.last_error}` : ''}
                </p>
              </div>
            )
          })}
        </div>
        <p className="field-help">
          账号规则独立生效，有独立规则的账号不会再被默认刷新重复查询。自定义服务需先配置余额查询方案。
        </p>
        <div className="operations-inset operations-form">
          <label className="switch-row">
            <span>
              <strong>默认刷新状态与余额</strong>
              <small>为未单独设置规则的账号使用统一间隔</small>
            </span>
            <input
              type="checkbox"
              checked={!!form.auto_refresh}
              onChange={(e) => update('auto_refresh', e.target.checked)}
            />
          </label>
          <label htmlFor="default-refresh">
            默认间隔（分钟）
            <input
              id="default-refresh"
              type="number"
              min="5"
              max="1440"
              required
              value={form.refresh_minutes || 30}
              onChange={(e) => update('refresh_minutes', Number(e.target.value))}
            />
          </label>
        </div>
      </section>
      <div className="settings-grid">
        <section className="content-card operations-form">
          <h2>额外福利</h2>
          <label className="switch-row">
            <span>
              <strong>自动领取福利</strong>
              <small>国内成长任务、连续签到奖励、猫猫旅行与 Qoder 福利</small>
            </span>
            <input
              type="checkbox"
              checked={!!form.auto_benefits}
              onChange={(e) => update('auto_benefits', e.target.checked)}
            />
          </label>
          <label htmlFor="benefits-time">
            福利执行时间
            <input
              id="benefits-time"
              type="time"
              value={form.checkin_time || '09:00'}
              onChange={(e) => update('checkin_time', e.target.value)}
            />
          </label>
          <div className="inline-actions">
            <button
              type="button"
              className="button secondary"
              disabled={!!busy || dirty}
              onClick={() => run('checkin')}
            >
              <Play size={14} />
              全部签到
            </button>
            <button
              type="button"
              className="button secondary"
              disabled={!!busy || dirty}
              onClick={() => run('refresh')}
            >
              <RefreshCw size={14} />
              全部刷新
            </button>
          </div>
        </section>
        <section className="content-card operations-form">
          <h2>任务告警</h2>
          <label htmlFor="automation-webhook">
            Webhook 地址
            <input
              id="automation-webhook"
              type="url"
              placeholder="https://example.com/webhook"
              value={form.webhook_url || ''}
              onChange={(e) => update('webhook_url', e.target.value)}
            />
          </label>
          <label htmlFor="automation-low-balance">
            低余额阈值
            <input
              id="automation-low-balance"
              type="number"
              min="0"
              step="0.01"
              value={form.low_balance || 0}
              onChange={(e) => update('low_balance', Number(e.target.value))}
            />
          </label>
          <label className="switch-row">
            <span>任务失败时通知</span>
            <input
              type="checkbox"
              checked={form.notify_failures !== false}
              onChange={(e) => update('notify_failures', e.target.checked)}
            />
          </label>
        </section>
      </div>
      <section className="content-card">
        <span className="section-label">执行历史</span>
        <h2>最近自动任务</h2>
        <div className="automation-history">
          {(status?.history || []).slice(0, 30).map((row, index) => (
            <div key={`${row.time}:${index}`}>
              <span className={`candidate-dot ${row.ok ? 'ready' : ''}`} />
              <div>
                <strong>{row.label || jobText[row.action]}</strong>
                <small>
                  {dateText(row.time)} · {row.manual ? '手动' : '自动'}
                </small>
              </div>
              <span>
                {row.ok ? '成功' : '失败'} · {row.summary?.succeeded || 0} / {row.summary?.total || 0}
              </span>
              {row.error && <p className="form-error">{row.error}</p>}
            </div>
          ))}
          {!status?.history?.length && <p className="empty-state">执行后会在这里显示结果。</p>}
        </div>
      </section>
    </form>
  )
}
