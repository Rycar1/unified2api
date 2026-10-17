export const beijingDate = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})
const beijingClock = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})
export function numeric(value) {
  if (value == null || value === '' || typeof value === 'boolean') return null
  const result = Number(value)
  return Number.isFinite(result) ? result : null
}
export function count(value) {
  const number = numeric(value)
  return number == null ? '—' : number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
export function tokenCount(value) {
  const number = numeric(value)
  if (number == null) return '—'
  if (!number) return '0'
  if (number > 0 && number < 10000) return '<0.01 M'
  if (number >= 1e8) return `${count(number / 1e8)} 亿`
  return `${count(number / 1e6)} M`
}
export function timestamp(value) {
  if (value == null || value === '') return null
  const number = numeric(value)
  const date =
    number != null && number > 0
      ? new Date(number > 1e11 ? number : number * 1000)
      : typeof value === 'string' && /(?:Z|[+-]\d{2}:?\d{2})$/.test(value)
        ? new Date(value)
        : null
  return date && Number.isFinite(date.getTime()) ? date : null
}
export function dateText(value, clock = true) {
  const date = timestamp(value)
  return date ? `${beijingDate.format(date)}${clock ? ' ' + beijingClock.format(date) : ''}` : '时间未提供'
}
export function clockText(value) {
  const date = timestamp(value)
  return date ? beijingClock.format(date) : '—'
}
export function platformLabel(account = {}) {
  if (account.provider === 'qoder') return account.region === 'cn' ? 'Qoder 国内' : 'Qoder 国际'
  return (
    {
      trae: 'TRAE',
      codebuddy: 'CodeBuddy 国内',
      monkeycode: 'MonkeyCode',
      workbuddy: 'WorkBuddy 国际',
      workbuddy_intl: 'WorkBuddy 国际',
      antigravity: 'Antigravity',
      qoder_cn: 'Qoder 国内',
      'qoder-cn': 'Qoder 国内',
    }[account.provider] ||
    account.provider ||
    '平台未提供'
  )
}
export const actionNames = {
  checkin: '每日签到',
  daily_activity: '网页活跃',
  daily_chat: '网页活跃',
  growth: '成长任务',
  streak: '连续签到奖励',
  travel: '猫猫旅行',
  pro_claim: 'Pro 福利',
  benefits: '领取福利',
  refresh: '余额刷新',
}
export function actionName(action) {
  return actionNames[action] || '账号任务'
}
export function accountName(row, accounts = []) {
  if (row.nickname) return row.nickname
  const provider = row.provider === 'workbuddy' ? 'workbuddy_intl' : row.provider
  const account = accounts.find(
    (item) =>
      (!provider || item.provider === provider || (provider.startsWith('qoder') && item.provider === 'qoder')) &&
      [item.id, item.uid].includes(row.account_id || row.uid),
  )
  return account?.name || account?.nickname || '历史账号'
}
export function executionState(row) {
  if (row.ok === true) return { key: 'success', label: '执行成功' }
  if (row.ok === false) return { key: 'danger', label: '执行失败' }
  if (['running', 'pending', 'started'].includes(row.state)) return { key: 'warning', label: '进行中' }
  return { key: 'neutral', label: '状态未提供' }
}
