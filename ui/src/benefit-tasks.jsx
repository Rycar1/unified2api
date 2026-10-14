import React from 'react'
import { CalendarDays, Coins, Gift, PawPrint, Sparkles } from 'lucide-react'
import { RecordEmpty } from './benefit-records'
import { count, numeric } from './record-format'

const states = {
  completed: ['primary', '待领取'],
  claimable: ['primary', '待领取'],
  claimed: ['success', '已领取'],
  accepted: ['warning', '进行中'],
  in_progress: ['warning', '进行中'],
  not_accepted: ['neutral', '未完成'],
  locked: ['neutral', '未解锁'],
  expired: ['warning', '已结束'],
}
const travels = { idle: '等待出发', traveling: '旅行中', arrived: '待领取奖励' }

function BenefitTask({ task }) {
  const [tone, label] = states[String(task.status || '').toLowerCase()] || ['neutral', '状态待确认']
  const current = numeric(task.current),
    target = numeric(task.target)
  const progress =
    current != null && current >= 0 && target > 0 ? Math.max(0, Math.min(100, (current / target) * 100)) : null
  const Icon = /checkin|daily/i.test(task.task_code || '')
    ? CalendarDays
    : /travel/i.test(task.task_code || '')
      ? PawPrint
      : Gift
  return (
    <article className="benefit-task">
      <header>
        <span className="grant-symbol">
          <Icon size={18} />
        </span>
        <h3>{task.name || task.title || '福利任务'}</h3>
        <span className={`record-badge tone-${tone}`}>{label}</span>
      </header>
      {task.description && <p>{task.description}</p>}
      {task.reason && task.reason !== task.description && <p className="task-requirement">{task.reason}</p>}
      <div className="benefit-task-rewards">
        {numeric(task.reward_credit) > 0 && (
          <span>
            <Coins size={14} />
            <strong>+{count(task.reward_credit)}</strong> 积分
          </span>
        )}
        {numeric(task.reward_energy) > 0 && (
          <span>
            <Sparkles size={14} />+{count(task.reward_energy)} 能量
          </span>
        )}
      </div>
      {progress != null && (
        <div className="benefit-task-progress">
          <span>
            任务进度{' '}
            <strong>
              {count(current)} / {count(target)}
            </strong>
          </span>
          <div
            className="grant-track"
            role="progressbar"
            aria-label={`${task.name || '任务'}进度`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={progress}
          >
            <i style={{ width: `${progress}%` }} />
          </div>
        </div>
      )}
    </article>
  )
}

export function BenefitTasks({ value, provider }) {
  const tasks = Array.isArray(value?.tasks) ? value.tasks : []
  const summary = value?.summary || {}
  const qoder = provider === 'qoder',
    domestic = provider === 'codebuddy'
  const metrics = [
    [qoder ? '积分余额' : '能量余额', qoder ? summary.credits?.remain : domestic ? summary.energy : null, Coins],
    ['连续签到', summary.streak_days, CalendarDays, ' 天'],
    ['补签卡', domestic && summary.makeup_cards > 0 ? summary.makeup_cards : null, Gift, ' 张'],
    ['抽奖机会', domestic && summary.lottery_chances > 0 ? summary.lottery_chances : null, Sparkles, ' 次'],
  ].filter(([, value]) => numeric(value) != null)
  const codes = Array.isArray(summary.codes) ? summary.codes.filter((row) => row.code) : []
  return (
    <div className="benefit-tasks">
      {!!metrics.length && (
        <div className="benefit-task-summary">
          {metrics.map(([label, amount, Icon, suffix]) => (
            <div key={label}>
              <Icon size={17} />
              <span>
                {label}
                <strong>
                  {count(amount)}
                  {suffix || ''}
                </strong>
              </span>
            </div>
          ))}
        </div>
      )}
      {domestic && travels[summary.travel?.state] && (
        <p className="task-travel">
          <PawPrint size={16} />
          猫猫旅行 · {travels[summary.travel.state]}
        </p>
      )}
      <div className="benefit-task-grid">
        {tasks.map((task, index) => (
          <BenefitTask task={task} key={`${task.task_code}-${index}`} />
        ))}
      </div>
      {!tasks.length && (
        <RecordEmpty title="暂无额外福利任务">{value?.msg || '可使用上方按钮执行签到或账号福利。'}</RecordEmpty>
      )}
      {!!codes.length && (
        <section className="benefit-codes">
          <h3>已领取兑换码</h3>
          {codes.map((row, index) => (
            <p key={index}>
              <span>{row.campaign || '活动福利'}</span>
              <code>{row.code}</code>
            </p>
          ))}
        </section>
      )}
    </div>
  )
}
