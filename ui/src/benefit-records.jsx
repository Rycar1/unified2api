import React, { useMemo, useState } from 'react'
import {
  ArrowDownLeft,
  CalendarDays,
  Check,
  CircleCheck,
  Clock3,
  Coins,
  Gift,
  Globe,
  Package,
  PawPrint,
  Search,
  Sparkles,
  TriangleAlert,
  WalletCards,
} from 'lucide-react'
import {
  accountName,
  actionName,
  clockText,
  count,
  dateText,
  executionState,
  numeric,
  platformLabel,
  timestamp,
} from './record-format'
import './benefit-records.css'

const actionIcons = {
  travel: PawPrint,
  growth: Sparkles,
  checkin: CalendarDays,
  streak: CircleCheck,
  daily_activity: Globe,
  daily_chat: Globe,
  pro_claim: Gift,
  benefits: Gift,
}
const creditState = {
  active: ['success', '使用中'],
  available: ['primary', '可用'],
  used_up: ['neutral', '已用完'],
  expired: ['warning', '已过期'],
}
const unitKey = (unit) => (['credit', 'credits', '积分'].includes(unit || 'credit') ? 'credit' : unit)
const unitName = (unit) =>
  unit === 'credit' ? '积分' : unit === 'token' || unit === 'tokens' ? 'Token' : unit || '额度'

export function RecordEmpty({ title, children }) {
  return (
    <div className="record-empty">
      <span>
        <Gift size={26} />
      </span>
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  )
}
export function RecordRing({ value, total, label, detail, tone = 'primary' }) {
  const ratio = total > 0 && value != null ? Math.max(0, Math.min(1, value / total)) : null
  return (
    <div className={`record-ring tone-${tone}`}>
      <svg
        viewBox="0 0 120 120"
        role="img"
        aria-label={`${label}：${ratio == null ? '未提供' : Math.round(ratio * 100) + '%'}`}
      >
        <circle cx="60" cy="60" r="49" className="ring-track" />
        {ratio != null && (
          <circle
            cx="60"
            cy="60"
            r="49"
            className="ring-value"
            pathLength="100"
            strokeDasharray={`${ratio * 100} 100`}
          />
        )}
      </svg>
      <div>
        <strong>{ratio == null ? '—' : `${Math.round(ratio * 100)}%`}</strong>
        <small>{label}</small>
      </div>
      {detail && <p>{detail}</p>}
    </div>
  )
}
function RecordStats({ stats }) {
  return (
    <div className="record-stats">
      {stats.map(([label, value, Icon, note]) => (
        <article key={label}>
          <span className="record-stat-icon">
            <Icon size={18} />
          </span>
          <div>
            <small>{label}</small>
            <strong>{value}</strong>
            {note && <p>{note}</p>}
          </div>
        </article>
      ))}
    </div>
  )
}
function RecordToolbar({ keyword, onKeyword, status, onStatus, options, resultCount, onClear, children }) {
  return (
    <>
      <div className="record-toolbar">
        <label className="record-search">
          <Search size={16} />
          <input
            aria-label="搜索记录"
            placeholder="搜索账号、任务或来源"
            value={keyword}
            onChange={(event) => onKeyword(event.target.value)}
          />
        </label>
        <select aria-label="筛选记录状态" value={status} onChange={(event) => onStatus(event.target.value)}>
          <option value="all">全部状态</option>
          {options.map(([key, label]) => (
            <option value={key} key={key}>
              {label}
            </option>
          ))}
        </select>
        {children}
        {onClear && (
          <button className="button secondary record-clear" aria-label="清除记录筛选" onClick={onClear}>
            清除
          </button>
        )}
      </div>
      {resultCount != null && (
        <p className="record-result-summary" role="status">
          找到 {resultCount} 条记录 · 上方为整体统计
        </p>
      )}
    </>
  )
}
function Pagination({ page, onPage, total, size = 10 }) {
  const pages = Math.max(1, Math.ceil(total / size))
  return (
    <div className="record-pagination">
      <span>
        {total} 条记录 · {page + 1} / {pages} 页
      </span>
      <div>
        <button className="button secondary" disabled={page === 0} onClick={() => onPage(page - 1)}>
          上一页
        </button>
        <button className="button secondary" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>
          下一页
        </button>
      </div>
    </div>
  )
}

export function ExecutionRecords({ value, accounts }) {
  const [keyword, setKeyword] = useState(''),
    [status, setStatus] = useState('all'),
    [page, setPage] = useState(0)
  const records = Array.isArray(value?.items) ? value.items : []
  const successes = records.filter((row) => row.ok === true).length
  const rewards = records.map((row) => numeric(row.earned_credit)).filter((value) => value != null)
  const filtered = useMemo(
    () =>
      records
        .filter(
          (row) =>
            (status === 'all' || executionState(row).key === status) &&
            `${accountName(row, accounts)} ${actionName(row.action)} ${row.message || ''} ${platformLabel(row)}`
              .toLowerCase()
              .includes(keyword.toLowerCase()),
        )
        .sort((a, b) => (timestamp(b.created)?.getTime() || 0) - (timestamp(a.created)?.getTime() || 0)),
    [records, status, keyword, accounts],
  )
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 10) - 1))
  const visible = filtered.slice(currentPage * 10, (currentPage + 1) * 10)
  const groups = []
  visible.forEach((row) => {
    const date = dateText(row.created, false)
    if (groups.at(-1)?.date !== date) groups.push({ date, rows: [] })
    groups.at(-1).rows.push(row)
  })
  return (
    <div className="benefit-records">
      <RecordStats
        stats={[
          ['当前记录', count(records.length), Clock3, '保留的任务执行记录'],
          ['执行成功', count(successes), CircleCheck, `${records.filter((row) => row.ok === false).length} 次失败`],
          [
            '已领取积分',
            rewards.length ? count(rewards.reduce((sum, value) => sum + value, 0)) : '—',
            Coins,
            '仅累计上游返回的奖励',
          ],
        ]}
      />
      <RecordToolbar
        keyword={keyword}
        onKeyword={(value) => {
          setKeyword(value)
          setPage(0)
        }}
        status={status}
        onStatus={(value) => {
          setStatus(value)
          setPage(0)
        }}
        resultCount={filtered.length}
        onClear={
          keyword || status !== 'all'
            ? () => {
                setKeyword('')
                setStatus('all')
                setPage(0)
              }
            : undefined
        }
        options={[
          ['success', '执行成功'],
          ['danger', '执行失败'],
          ['warning', '进行中'],
          ['neutral', '状态未提供'],
        ]}
      />
      <div className="execution-layout">
        <div className="execution-timeline">
          {!filtered.length && (
            <RecordEmpty title={records.length ? '没有匹配的记录' : '还没有执行记录'}>
              完成签到或福利任务后，记录会出现在这里。
            </RecordEmpty>
          )}
          {groups.map((group) => (
            <section className="timeline-day" key={group.date}>
              <h3>
                <CalendarDays size={14} />
                {group.date}
              </h3>
              <ol>
                {group.rows.map((row, index) => {
                  const state = executionState(row),
                    Icon = actionIcons[row.action] || Gift
                  return (
                    <li
                      className={`timeline-item tone-${state.key}`}
                      key={`${row.provider}-${row.account_id}-${row.created}-${index}`}
                    >
                      <div className="timeline-symbol">
                        <Icon size={18} />
                      </div>
                      <div className="timeline-body">
                        <div className="timeline-title">
                          <h4>{actionName(row.action)}</h4>
                          <span className={`record-badge tone-${state.key}`}>
                            {state.key === 'success' ? (
                              <Check size={12} />
                            ) : state.key === 'danger' ? (
                              <TriangleAlert size={12} />
                            ) : (
                              <Clock3 size={12} />
                            )}
                            {state.label}
                          </span>
                          <time>{clockText(row.created)}</time>
                        </div>
                        <div className="record-byline">
                          {accountName(row, accounts)}
                          <span>·</span>
                          {platformLabel(row)}
                        </div>
                        {row.message && <p className="timeline-message">{row.message}</p>}
                        <div className="timeline-rewards">
                          {numeric(row.earned_credit) != null && (
                            <span>
                              <Coins size={13} />
                              奖励 <strong>{count(row.earned_credit)}</strong> 积分
                            </span>
                          )}
                          {numeric(row.remaining) != null && (
                            <span>
                              <WalletCards size={13} />
                              余额 <strong>{count(row.remaining)}</strong>
                            </span>
                          )}
                        </div>
                      </div>
                    </li>
                  )
                })}
              </ol>
            </section>
          ))}
        </div>
        <aside className="execution-insight">
          <RecordRing
            value={successes}
            total={records.filter((row) => typeof row.ok === 'boolean').length}
            label="执行成功率"
            tone="success"
          />
          <h3>记录概况</h3>
          <p>任务按北京时间排列。执行成功不代表额度已到账，奖励以返回的积分和发放明细为准。</p>
          <div className="task-type-list">
            {Object.entries(records.reduce((all, row) => ({ ...all, [row.action]: (all[row.action] || 0) + 1 }), {}))
              .sort((a, b) => b[1] - a[1])
              .slice(0, 5)
              .map(([action, amount]) => {
                const Icon = actionIcons[action] || Gift
                return (
                  <div key={action}>
                    <Icon size={15} />
                    <span>{actionName(action)}</span>
                    <strong>{amount}</strong>
                  </div>
                )
              })}
          </div>
        </aside>
      </div>
      <Pagination page={currentPage} onPage={setPage} total={filtered.length} />
    </div>
  )
}

export function CreditGrants({ value }) {
  const records = Array.isArray(value?.rows) ? value.rows : []
  const units = [...new Set(records.map((row) => unitKey(row.unit)))]
  const [selectedUnit, setUnit] = useState('credit'),
    [keyword, setKeyword] = useState(''),
    [status, setStatus] = useState('all'),
    [page, setPage] = useState(0)
  const unit = units.includes(selectedUnit) ? selectedUnit : units[0] || 'credit'
  const subset = records.filter((row) => unitKey(row.unit) === unit)
  const accountCount = new Set(subset.map((row) => row.uid).filter(Boolean)).size
  const filtered = subset.filter(
    (row) =>
      (status === 'all' || row.status === status) &&
      `${row.nickname || ''} ${row.name || ''} ${row.grant_reason || ''} ${row.product || ''}`
        .toLowerCase()
        .includes(keyword.toLowerCase()),
  )
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filtered.length / 10) - 1))
  const available = subset.filter((row) => ['active', 'available'].includes(row.status))
  const availableAmounts = available.map((row) => numeric(row.remain)).filter((value) => value != null)
  const availabilityUnknown =
    !subset.length ||
    subset.some((row) => !creditState[row.status] && numeric(row.remain) !== 0) ||
    available.some((row) => numeric(row.remain) == null)
  const availableTotal = availabilityUnknown ? '—' : count(availableAmounts.reduce((sum, value) => sum + value, 0))
  const sizes = subset.map((row) => numeric(row.size)).filter((value) => value != null)
  const measured = subset.filter(
    (row) =>
      numeric(row.used) >= 0 && numeric(row.used) != null && numeric(row.remain) >= 0 && numeric(row.remain) != null,
  )
  const measuredRemain = measured.reduce((sum, row) => sum + numeric(row.remain), 0)
  const measuredUsed = measured.reduce((sum, row) => sum + numeric(row.used), 0)
  return (
    <div className="benefit-records">
      <RecordStats
        stats={[
          ['发放记录', count(subset.length), Package, accountCount ? `${accountCount} 个账号` : undefined],
          [
            '记录总额度',
            sizes.length ? count(sizes.reduce((sum, value) => sum + value, 0)) : '—',
            Gift,
            unitName(unit),
          ],
          ['可用剩余', availableTotal, WalletCards, '排除已过期和已用完的额度包'],
        ]}
      />
      <div className="grant-overview">
        <RecordRing value={measuredUsed} total={measuredUsed + measuredRemain} label="已使用比例" />
        <div>
          <span className="section-label">额度构成</span>
          <h3>额度使用概况</h3>
          <div className="grant-legend">
            <span>
              <i className="legend-used" />
              已使用 <strong>{measured.length ? count(measuredUsed) : '—'}</strong>
            </span>
            <span>
              <i />
              全部记录剩余 <strong>{measured.length ? count(measuredRemain) : '—'}</strong>
            </span>
          </div>
          <p className="record-byline">
            {unitName(unit)} · {measured.length} 个额度包提供了用量数据
          </p>
        </div>
        {timestamp(value?.fetched_at) && (
          <div className="grant-synced">
            <Clock3 size={15} />
            <span>
              最近同步<strong>{dateText(value?.fetched_at)}</strong>
            </span>
          </div>
        )}
      </div>
      <RecordToolbar
        keyword={keyword}
        onKeyword={(value) => {
          setKeyword(value)
          setPage(0)
        }}
        status={status}
        onStatus={(value) => {
          setStatus(value)
          setPage(0)
        }}
        options={Object.entries(creditState).map(([key, state]) => [key, state[1]])}
        resultCount={filtered.length}
        onClear={
          keyword || status !== 'all'
            ? () => {
                setKeyword('')
                setStatus('all')
                setPage(0)
              }
            : undefined
        }
      >
        {units.length > 1 && (
          <select
            aria-label="额度单位"
            value={unit}
            onChange={(event) => {
              setUnit(event.target.value)
              setPage(0)
            }}
          >
            {units.map((unit) => (
              <option key={unit} value={unit}>
                {unitName(unit)}
              </option>
            ))}
          </select>
        )}
      </RecordToolbar>
      <div className="grant-cards">
        {filtered.slice(currentPage * 10, (currentPage + 1) * 10).map((row, index) => {
          const [tone, label] = creditState[row.status] || ['neutral', '状态未提供']
          const used = numeric(row.used),
            remain = numeric(row.remain)
          const total = used != null && remain != null && used >= 0 && remain >= 0 ? used + remain : null
          const percent = total > 0 ? Math.max(0, Math.min(100, (used / total) * 100)) : null
          return (
            <article className={`grant-card tone-${tone}`} key={`${row.uid}-${row.create_at}-${row.name}-${index}`}>
              <header>
                <span className="grant-symbol">
                  <Gift size={19} />
                </span>
                <div>
                  <h3>{row.name || '额度包'}</h3>
                  {row.nickname && <p>{row.nickname}</p>}
                </div>
                <span className={`record-badge tone-${tone}`}>{label}</span>
              </header>
              <div className="grant-amount">
                <strong>{count(row.size)}</strong>
                <span>
                  {unitName(unit)}
                  <small>发放额度</small>
                </span>
              </div>
              {(row.grant_reason || row.product) && (
                <p className="grant-source">
                  <ArrowDownLeft size={14} />
                  {row.grant_reason || row.product}
                </p>
              )}
              <div className="grant-meter">
                <div>
                  <span>已用 {count(used)}</span>
                  <strong>{percent == null ? '用量比例未提供' : `${percent.toFixed(1)}%`}</strong>
                  <span>剩余 {count(remain)}</span>
                </div>
                <div
                  className="grant-track"
                  role={percent == null ? undefined : 'progressbar'}
                  aria-label={`${row.name || '额度包'} 已使用比例`}
                  aria-valuemin={percent == null ? undefined : 0}
                  aria-valuemax={percent == null ? undefined : 100}
                  aria-valuenow={percent ?? undefined}
                >
                  <i style={{ width: `${percent ?? 0}%` }} />
                </div>
              </div>
              {(timestamp(row.create_at) || timestamp(row.expire_at) || row.no_expiry) && (
                <footer>
                  {timestamp(row.create_at) && (
                    <span>
                      <CalendarDays size={13} />
                      {dateText(row.create_at)}
                    </span>
                  )}
                  {(row.no_expiry || timestamp(row.expire_at)) && (
                    <span>
                      <Clock3 size={13} />
                      {row.no_expiry ? '长期有效' : `到期：${dateText(row.expire_at, false)}`}
                    </span>
                  )}
                </footer>
              )}
            </article>
          )
        })}
      </div>
      {!filtered.length && (
        <RecordEmpty title={records.length ? '没有匹配的额度包' : '暂无额度发放记录'}>
          账号同步额度包后，这里会显示来源、用量和有效期。
        </RecordEmpty>
      )}
      <Pagination page={currentPage} onPage={setPage} total={filtered.length} />
    </div>
  )
}
