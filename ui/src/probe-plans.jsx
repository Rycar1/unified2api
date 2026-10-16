import React, { useEffect, useRef, useState } from 'react'
import {
  Activity,
  CalendarClock,
  Check,
  Play,
  Plus,
  RefreshCw,
  Save,
  X,
} from 'lucide-react'
import './reliability.css'

const checks = {
  chat: '普通调用',
  stream: '流式结束',
  responses: 'Responses',
  messages: 'Messages',
  tools: '工具调用',
  reasoning: '推理输出',
}
const dateText = (n) =>
  n
    ? new Date(n * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })
    : '未安排'
const outcomeText = (r) =>
  r.outcome === 'running'
    ? '执行中'
    : r.ok
      ? '通过'
      : r.outcome === 'capability_unverified'
        ? '能力未验证'
        : r.diagnosis?.label ||
          { timeout: '检测超时', interrupted: '检测中断' }[r.outcome] ||
          '未通过'

export function ProbePlansPage({ data, request, csrf, DataTable }) {
  const [status, setStatus] = useState(null),
    [form, setForm] = useState(null)
  const [model, setModel] = useState(''),
    [busy, setBusy] = useState(''),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const dirty = useRef(false)
  const models = data?.models || []
  async function load() {
    const value = await request('unified/probe-plans')
    setStatus(value)
    if (!dirty.current) setForm(value.settings)
  }
  useEffect(() => {
    let live = true
    const refresh = () => {
      if (live)
        load().catch((e) => {
          if (live) setError(e.message)
        })
    }
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [])
  function update(key, value) {
    dirty.current = true
    setForm((f) => ({ ...f, [key]: value }))
  }
  async function save(event) {
    event.preventDefault()
    setBusy('save')
    setError('')
    setMessage('')
    try {
      await request('unified/probe-plans', {
        method: 'PATCH',
        body: form,
        csrf,
      })
      dirty.current = false
      await load()
      setMessage('巡检计划已保存')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  async function run() {
    setBusy('run')
    setError('')
    setMessage('')
    try {
      await request('unified/probe-plans/run', {
        method: 'POST',
        body: {},
        csrf,
      })
      await load()
      setMessage('巡检已开始，结果会自动更新')
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy('')
    }
  }
  function addModel() {
    if (!models.some((m) => m.id === model)) {
      setError('请从模型目录选择完整 ID')
      return
    }
    if (form.models.length >= 10) {
      setError('最多选择 10 个模型')
      return
    }
    update('models', [...new Set([...form.models, model])])
    setModel('')
    setError('')
  }
  if (!form || !status)
    return (
      <p
        className={error ? 'form-error' : 'muted'}
        role={error ? 'alert' : 'status'}
      >
        {error || '正在读取巡检计划…'}
      </p>
    )
  const usage = status.usage
  return (
    <div className="stack reliability-stack">
      <section className="content-card probe-overview">
        <div className="section-head">
          <div>
            <span className="section-label">兼容性巡检</span>
            <h2>
              {status.running
                ? '正在验证模型接口'
                : form.enabled
                  ? '自动巡检已开启'
                  : '手动巡检'}
            </h2>
            <p className="muted">只发送独立合成请求，不重放用户对话。</p>
          </div>
          <button
            className="button primary"
            onClick={run}
            disabled={
              !!busy ||
              status.running ||
              dirty.current ||
              !status.settings.models.length
            }
          >
            <Play size={15} />
            立即巡检
          </button>
        </div>
        <div className="probe-meters">
          {[
            ['请求', usage.requests, status.settings.daily_requests],
            ['Token', usage.tokens, status.settings.daily_tokens],
          ].map(([label, used, limit]) => (
            <div key={label}>
              <div>
                <span>今日检测{label}</span>
                <strong>
                  {used.toLocaleString()}{' '}
                  <small>/ {limit.toLocaleString()}</small>
                </strong>
              </div>
              <div
                className="probe-meter"
                role="meter"
                aria-label={`今日巡检${label}`}
                aria-valuenow={used}
                aria-valuemin={0}
                aria-valuemax={Math.max(used, limit)}
              >
                <i
                  style={{ width: `${Math.min(100, (used / limit) * 100)}%` }}
                />
              </div>
            </div>
          ))}
        </div>
        <div className="reliability-meta">
          <span>
            <CalendarClock size={14} />
            下次执行：{dateText(status.next_at)}
          </span>
          <span>每日预算按北京时间重置</span>
        </div>
      </section>
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">计划</span>
            <h2>检测哪些模型与能力</h2>
          </div>
          <span className="muted">最多 10 个模型</span>
        </div>
        <form onSubmit={save}>
          <div className="probe-model-entry">
            <label htmlFor="probe-model">
              选择模型
              <input
                id="probe-model"
                list="probe-catalog"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder="输入前缀或模型名称"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    addModel()
                  }
                }}
              />
            </label>
            <datalist id="probe-catalog">
              {models.map((m) => (
                <option key={m.id} value={m.id} />
              ))}
            </datalist>
            <button
              type="button"
              className="button secondary"
              onClick={addModel}
            >
              <Plus size={15} />
              添加模型
            </button>
          </div>
          <div className="probe-model-tags">
            {form.models.map((m) => (
              <span key={m}>
                <code>{m}</code>
                <button
                  type="button"
                  aria-label={`移除巡检模型 ${m}`}
                  onClick={() =>
                    update(
                      'models',
                      form.models.filter((id) => id !== m),
                    )
                  }
                >
                  <X size={13} />
                </button>
              </span>
            ))}
          </div>
          <div className="probe-checks">
            {Object.entries(checks).map(([id, label]) => (
              <label
                key={id}
                className={form.checks.includes(id) ? 'selected' : ''}
              >
                <input
                  type="checkbox"
                  checked={form.checks.includes(id)}
                  onChange={(e) =>
                    update(
                      'checks',
                      e.target.checked
                        ? [...form.checks, id]
                        : form.checks.filter((v) => v !== id),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <div className="reliability-numbers">
            {[
              ['interval_minutes', '巡检间隔（分钟）', 5, 1440],
              ['daily_requests', '每日请求预算', 1, 1000],
              ['daily_tokens', '每日 Token 预算', 512, 10000000],
              ['max_tokens', '每次输出上限', 32, 2048],
            ].map(([key, label, min, max]) => (
              <label key={key}>
                {label}
                <input
                  required
                  type="number"
                  min={min}
                  max={max}
                  step="1"
                  value={form[key]}
                  onChange={(e) => update(key, Number(e.target.value))}
                />
              </label>
            ))}
          </div>
          <div className="reliability-switches">
            <label>
              <input
                type="checkbox"
                checked={form.enabled}
                onChange={(e) => update('enabled', e.target.checked)}
              />
              定时执行
            </label>
            <label>
              <input
                type="checkbox"
                checked={form.notify_failures}
                onChange={(e) => update('notify_failures', e.target.checked)}
              />
              新故障通过已配置的 Webhook 提醒
            </label>
          </div>
          <p className="field-help">
            每次调用前预占预算，预算不足时停止本轮。推理与工具检测须看到实际输出证据；未验证不代表模型一定不支持。巡检用量单独统计。
          </p>
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {message && (
            <p className="success-text" role="status">
              {message}
            </p>
          )}
          <button
            className="button primary"
            disabled={!!busy || status.running}
          >
            <Save size={15} />
            {busy === 'save' ? '保存中…' : '保存计划'}
          </button>
          {dirty.current && (
            <span className="muted reliability-inline">有未保存修改</span>
          )}
        </form>
      </section>
      <section className="content-card">
        <div className="section-head">
          <div>
            <span className="section-label">检测结果</span>
            <h2>最近巡检</h2>
          </div>
          <button
            className="button secondary"
            onClick={() => load().catch((e) => setError(e.message))}
          >
            <RefreshCw size={14} />
            刷新
          </button>
        </div>
        <DataTable
          columns={['执行时间', '实际请求模型', '检查项目', '结果', '耗时']}
          empty={
            !status.history.length && '保存模型后点击立即巡检，或开启定时执行'
          }
        >
          {status.history.map((r) => (
            <tr key={r.id}>
              <td className="muted">{dateText(r.started)}</td>
              <td>
                <code>{r.model}</code>
              </td>
              <td>{checks[r.check]}</td>
              <td>
                <span
                  className={`probe-result ${r.ok ? 'pass' : r.outcome === 'running' || r.outcome === 'capability_unverified' ? 'pending' : 'fail'}`}
                >
                  {r.ok ? <Check size={12} /> : <Activity size={12} />}
                  {outcomeText(r)}
                </span>
                {r.upstream_code && (
                  <small className="muted">上游代码 {r.upstream_code}</small>
                )}
                {r.diagnosis && (
                  <small className="muted">{r.diagnosis.suggestion}</small>
                )}
              </td>
              <td>
                {r.duration_ms == null
                  ? '—'
                  : `${r.duration_ms.toLocaleString()} ms`}
              </td>
            </tr>
          ))}
        </DataTable>
      </section>
    </div>
  )
}
