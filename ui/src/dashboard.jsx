import React, { useEffect, useId, useState } from 'react'
import {
  Activity,
  ArrowRight,
  Bot,
  Check,
  ChevronRight,
  Clock3,
  Copy,
  KeyRound,
  Layers,
  Network,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
  Users,
  Zap,
} from 'lucide-react'
import { actionName, count, dateText, numeric, platformLabel, tokenCount } from './record-format'
import './dashboard.css'

function Metric({ label, value, note, Icon }) {
  return (
    <article className="dashboard-metric">
      <span>
        <Icon size={19} />
      </span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
        <p>{note}</p>
      </div>
    </article>
  )
}
function PanelHeading({ label, title, onOpen }) {
  return (
    <div className="dashboard-panel-heading">
      <div>
        <span className="section-label">{label}</span>
        <h2>{title}</h2>
      </div>
      {onOpen && (
        <button className="link-button" aria-label={`查看${title}`} onClick={onOpen}>
          查看详情
          <ArrowRight size={14} />
        </button>
      )}
    </div>
  )
}
function UsageTrend({ result, loading, error, onNavigate }) {
  const [hovered, setHovered] = useState(null)
  const gradient = useId().replaceAll(':', '')
  const series = Array.isArray(result?.series) ? result.series : []
  const values = series.map((row) => (numeric(row.input_tokens) || 0) + (numeric(row.output_tokens) || 0))
  const peak = Math.max(1, ...values)
  const coords = values.map((value, index) => [
    series.length === 1 ? 280 : 20 + (index * 520) / (series.length - 1),
    145 - (value / peak) * 115,
  ])
  const points = coords.map((point) => point.join(',')).join(' ')
  return (
    <section className="content-card dashboard-trend">
      <PanelHeading label="用量趋势" title="近 7 天 Token" onOpen={() => onNavigate('usage')} />
      <div className="dashboard-trend-total">
        <strong>
          {loading
            ? '…'
            : result
              ? tokenCount((numeric(result.input_tokens) || 0) + (numeric(result.output_tokens) || 0))
              : '—'}
        </strong>
        <span>tokens</span>
        <small>{result ? `${count(result.requests)} 次请求` : error ? '统计暂时不可用' : '正在读取统计'}</small>
      </div>
      {loading ? (
        <div className="dashboard-chart-empty">
          <RefreshCw size={24} className="spin" />
          <p>正在读取用量</p>
        </div>
      ) : series.length ? (
        <div
          className="dashboard-chart"
          role="group"
          aria-label={`近7天Token走势，共${count(values.reduce((a, b) => a + b, 0))} tokens`}
        >
          <svg viewBox="0 0 560 170" preserveAspectRatio="none">
            <defs>
              <linearGradient id={gradient} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" stopColor="currentColor" stopOpacity=".22" />
                <stop offset="1" stopColor="currentColor" stopOpacity="0" />
              </linearGradient>
            </defs>
            {[30, 88, 145].map((y) => (
              <line key={y} x1="20" y1={y} x2="540" y2={y} className="dashboard-gridline" />
            ))}
            {coords.length > 1 && <polygon points={`20,155 ${points} 540,155`} fill={`url(#${gradient})`} />}
            <polyline
              points={points}
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
            {coords.map(([x, y], index) => (
              <circle
                cx={x}
                cy={y}
                r="4"
                key={series[index].day}
                tabIndex="0"
                role="img"
                aria-label={`${series[index].day}，${tokenCount(values[index])} tokens`}
                onPointerEnter={() => setHovered(index)}
                onPointerLeave={() => setHovered(null)}
                onFocus={() => setHovered(index)}
                onBlur={() => setHovered(null)}
              >
                <title>
                  {series[index].day} · {count(values[index])} tokens
                </title>
              </circle>
            ))}
          </svg>
          {hovered != null && series[hovered] && (
            <div
              className="dashboard-point-tooltip"
              role="status"
              style={{ left: `${Math.max(20, Math.min(80, (coords[hovered][0] / 560) * 100))}%` }}
            >
              <strong>{series[hovered].day}</strong>
              <span>{tokenCount(values[hovered])} tokens</span>
            </div>
          )}
          <div className="dashboard-chart-dates">
            {series.map((row) => (
              <span key={row.day}>{String(row.day).slice(5)}</span>
            ))}
          </div>
        </div>
      ) : (
        <div className="dashboard-chart-empty">
          <TrendingUp size={34} />
          <h3>{error ? '用量统计暂时不可用' : '还没有调用数据'}</h3>
          <p>{error ? error : '完成请求后，这里会显示实际用量趋势'}</p>
          <button className="link-button" onClick={() => onNavigate('test')}>
            发送测试请求
            <ArrowRight size={13} />
          </button>
        </div>
      )}
      <div className="dashboard-trend-footer">
        <span>
          <ShieldCheck size={14} />
          成功率 <strong>{result?.success_rate == null ? '—' : `${count(result.success_rate)}%`}</strong>
        </span>
        <span>
          <Clock3 size={14} />
          平均首 Token <strong>{result?.average_ttft_ms == null ? '—' : `${count(result.average_ttft_ms)} ms`}</strong>
        </span>
      </div>
    </section>
  )
}
function AccountHealth({ data, onNavigate }) {
  const accounts = data.accounts || [],
    ready = accounts.filter((row) => row.enabled !== false && ['ready', 'available'].includes(row.status))
  const groups = new Map()
  for (const account of accounts) {
    const label = platformLabel(account)
    if (!groups.has(label)) groups.set(label, [])
    groups.get(label).push(account)
  }
  return (
    <section className="content-card dashboard-health">
      <PanelHeading label="账号池" title="平台连接情况" onOpen={() => onNavigate('accounts')} />
      <div className="dashboard-health-total">
        <span className="dashboard-health-symbol">
          <Users size={23} />
        </span>
        <div>
          <strong>
            {ready.length}
            <small> / {accounts.length}</small>
          </strong>
          <p>当前可用账号</p>
        </div>
        <span
          className={`record-badge tone-${!accounts.length ? 'neutral' : accounts.length > ready.length ? 'warning' : 'success'}`}
        >
          {!accounts.length
            ? '尚未添加账号'
            : accounts.length === ready.length
              ? '账号状态正常'
              : `${accounts.length - ready.length} 个待处理或停用`}
        </span>
      </div>
      <div className="dashboard-platforms">
        {[...groups].map(([label, rows]) => {
          const usable = rows.filter(
            (row) => row.enabled !== false && ['ready', 'available'].includes(row.status),
          ).length
          return (
            <div key={label}>
              <div>
                <span>{label}</span>
                <small>
                  {usable} / {rows.length} 可用
                </small>
              </div>
              <div className="dashboard-platform-track">
                <i style={{ width: `${(usable / rows.length) * 100}%` }} />
              </div>
            </div>
          )
        })}
      </div>
      {!accounts.length && <p className="subtle-note">添加账号后，可在这里查看各平台的可用情况。</p>}
      <div className="dashboard-health-note">
        <i />
        可用{' '}
        <span>
          <i />
          冷却、异常或停用
        </span>
      </div>
    </section>
  )
}
function AutomationDigest({ data, onNavigate }) {
  const settings = data.automation || {},
    history = (data.automation_history || []).slice(0, 3)
  const scheduled = [
    ['每日签到', settings.auto_checkin, `每日 ${settings.checkin_time || '09:00'}`],
    ['自动领取福利', settings.auto_benefits, `每日 ${settings.checkin_time || '09:00'}`],
    ['余额刷新', settings.auto_refresh, `每 ${settings.refresh_minutes || 30} 分钟`],
  ]
  return (
    <section className="content-card dashboard-automation">
      <PanelHeading label="自动化" title="让账号保持就绪" onOpen={() => onNavigate('automation')} />
      <div className="dashboard-schedules">
        {scheduled.map(([name, enabled, detail]) => (
          <div key={name}>
            <span className={enabled ? 'schedule-light on' : 'schedule-light'} />
            <strong>{name}</strong>
            <small>{enabled ? detail : '未开启'}</small>
          </div>
        ))}
      </div>
      <div className="dashboard-recent-heading">
        最近任务<span>北京时间</span>
      </div>
      {history.length ? (
        <ul className="dashboard-recent">
          {history.map((row, index) => (
            <li key={`${row.time}-${row.action}-${index}`}>
              <span
                className={`record-badge tone-${row.ok === true ? 'success' : row.ok === false ? 'danger' : 'neutral'}`}
              >
                {row.ok === true ? '已完成' : row.ok === false ? '有失败' : '未知'}
              </span>
              <span>{actionName(row.action)}</span>
              <time>{dateText(row.time)}</time>
            </li>
          ))}
        </ul>
      ) : (
        <div className="dashboard-recent-empty">
          <Clock3 size={18} />
          <span>暂无自动任务记录</span>
        </div>
      )}
    </section>
  )
}
function ConnectionDigest({ data, onNavigate }) {
  const routes = (data.routes || []).filter((row) => row.enabled !== false),
    keys = data.keys || []
  return (
    <section className="content-card dashboard-connect">
      <PanelHeading label="统一接入" title="准备好下一次调用" onOpen={() => onNavigate('clients')} />
      <div className="dashboard-resource-counts">
        <div>
          <KeyRound size={19} />
          <strong>{keys.length}</strong>
          <small>访问密钥</small>
        </div>
        <div>
          <Network size={19} />
          <strong>{routes.length}</strong>
          <small>启用路由</small>
        </div>
        <div>
          <Layers size={19} />
          <strong>{(data.connections || []).length}</strong>
          <small>自定义服务</small>
        </div>
      </div>
      <div className="dashboard-shortcuts">
        {[
          [Users, '管理账号', 'accounts'],
          [KeyRound, '创建密钥', 'keys'],
          [Zap, '测试连接', 'test'],
        ].map(([Icon, name, page]) => (
          <button onClick={() => onNavigate(page)} key={page}>
            <Icon size={15} />
            <span>{name}</span>
            <ChevronRight size={14} />
          </button>
        ))}
      </div>
      {routes.length > 0 && (
        <p className="dashboard-route-hint">
          常用路由 <code>{`route/${routes[0].id}`}</code>
        </p>
      )}
    </section>
  )
}
export function Dashboard({ data, onNavigate, request }) {
  const [statistics, setStatistics] = useState({ loading: true, value: null, error: '' }),
    [copied, setCopied] = useState(false),
    [copyError, setCopyError] = useState('')
  useEffect(() => {
    let live = true
    setStatistics((current) => ({ ...current, loading: true, error: '' }))
    request('unified/performance?days=7')
      .then((value) => live && setStatistics({ loading: false, value, error: '' }))
      .catch((error) => live && setStatistics({ loading: false, value: null, error: error.message }))
    return () => {
      live = false
    }
  }, [request, data])
  const usable = (data.accounts || []).filter(
    (row) => row.enabled !== false && ['ready', 'available'].includes(row.status),
  ).length
  const modelCount = (data.models || []).length
  async function copyEndpoint() {
    try {
      await navigator.clipboard.writeText(`${location.origin}/v1`)
      setCopied(true)
      setCopyError('')
    } catch {
      setCopyError('复制失败，请选中接口地址手动复制')
    }
  }
  return (
    <div className="dashboard">
      <div className="dashboard-metrics">
        <Metric
          label="已连接账号"
          value={count(data.accounts?.length || 0)}
          note={`${usable} 个当前可用`}
          Icon={Users}
        />
        <Metric
          label="模型目录"
          value={count(modelCount)}
          note={`${new Set((data.models || []).map((row) => String(row.id).split('/')[0])).size} 个模型前缀`}
          Icon={Bot}
        />
        <Metric
          label="近 7 天请求"
          value={statistics.loading ? '…' : count(statistics.value?.requests)}
          note="按保留的调用记录统计"
          Icon={Activity}
        />
        <Metric
          label="实时进行中"
          value={count(data.metrics?.in_flight ?? 0)}
          note="当前正在处理的请求"
          Icon={Sparkles}
        />
      </div>
      <div className="dashboard-main-grid">
        <UsageTrend
          result={statistics.value}
          loading={statistics.loading}
          error={statistics.error}
          onNavigate={onNavigate}
        />
        <AccountHealth data={data} onNavigate={onNavigate} />
      </div>
      <div className="dashboard-bottom-grid">
        <AutomationDigest data={data} onNavigate={onNavigate} />
        <ConnectionDigest data={data} onNavigate={onNavigate} />
      </div>
      <section className="content-card dashboard-endpoint">
        <div>
          <span className="summary-icon">
            <Network size={18} />
          </span>
          <div>
            <strong>统一 API 地址</strong>
            <p>使用模型前缀选择平台，或通过智能路由调用。</p>
          </div>
        </div>
        <code>{location.origin}/v1</code>
        <button className="button secondary" onClick={copyEndpoint}>
          {copied ? <Check size={14} /> : <Copy size={14} />}
          {copied ? '已复制' : '复制地址'}
        </button>
      </section>
      {copyError && (
        <p className="form-error" role="alert">
          {copyError}
        </p>
      )}
    </div>
  )
}
