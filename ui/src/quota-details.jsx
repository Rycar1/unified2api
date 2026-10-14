import React from 'react'
import { RecordEmpty } from './benefit-records'
import { count } from './record-format'

// Deliberate allowlist: internal metadata and unknown fields never become UI labels.
const fields = [
  ['名称', ['name', 'title', 'PackageName']],
  ['套餐', ['plan']],
  ['余额', ['remaining', 'remain', 'CapacityRemainPrecise', 'CapacityRemain']],
  ['总额度', ['total', 'capacity', 'limit', 'size', 'CapacitySizePrecise', 'CapacitySize']],
  ['已用额度', ['used']],
  ['本周期余额', ['CycleCapacityRemainPrecise', 'CycleCapacityRemain']],
  ['本周期额度', ['CycleCapacitySizePrecise', 'CycleCapacitySize']],
  ['每日 Token 余额', ['daily_token_balance']],
  ['额度包数', ['packages', 'packs']],
  ['发放来源', ['grant_reason', 'GrantReason', 'CreatedReason', 'reason']],
  ['发放时间', ['create_time', 'created_at', 'create_iso']],
  ['到期时间', ['expires_at', 'expireTime', 'expire_time', 'expire_iso', 'PackageEndTime']],
  ['开始时间', ['PackageStartTime']],
  ['单位', ['unit', 'currency']],
  ['说明', ['description']],
]
const groups = {
  credits: '积分明细',
  quota: '额度明细',
  userQuota: '个人额度',
  packages: '额度包',
  details: '额度包',
  grants: '发放明细',
  data: '额度明细',
  items: '明细',
}
const present = (value) => value != null && (typeof value !== 'string' || (value.trim() !== '' && value.trim() !== '-'))
function project(value, depth = 0) {
  if (!value || typeof value !== 'object' || depth > 5) return null
  if (Array.isArray(value)) {
    const rows = value
      .slice(0, 100)
      .map((row) => project(row, depth + 1))
      .filter(Boolean)
    return rows.length ? rows : null
  }
  const simple = fields.flatMap(([label, aliases]) => {
    if (label === '到期时间' && value.no_expiry === true) return []
    const key = aliases.find(
      (key) => present(value[key]) && typeof value[key] !== 'object' && typeof value[key] !== 'boolean',
    )
    return key ? [[label, value[key]]] : []
  })
  if (value.no_expiry === true) simple.push(['有效期', '长期有效'])
  if (value.in_usage === true) simple.push(['状态', '使用中'])
  const nested = Object.entries(groups).flatMap(([key, label]) => {
    const result = project(value[key], depth + 1)
    return result ? [[label, result]] : []
  })
  return simple.length || nested.length ? { simple, nested } : null
}
function QuotaFields({ value }) {
  if (Array.isArray(value))
    return (
      <div className="task-list">
        {value.map((row, i) => (
          <div className="quota-entry" key={i}>
            <QuotaFields value={row} />
          </div>
        ))}
      </div>
    )
  return (
    <>
      {!!value.simple.length && (
        <dl className="details-pairs">
          {value.simple.map(([label, amount]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{typeof amount === 'number' ? count(amount) : amount}</dd>
            </div>
          ))}
        </dl>
      )}
      {value.nested.map(([label, rows]) => (
        <section key={label}>
          <h3 className="detail-label">{label}</h3>
          <QuotaFields value={rows} />
        </section>
      ))}
    </>
  )
}
export function QuotaDetails({ value }) {
  const details = project(value)
  return details ? (
    <QuotaFields value={details} />
  ) : (
    <RecordEmpty title="暂无可展示的额度明细">上游暂未提供余额或额度包信息。</RecordEmpty>
  )
}
