import React, { useId, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, Clock, FlaskConical, LoaderCircle, X } from 'lucide-react'
import './account-probe.css'

export function accountModelPrefix(account) {
  return account.provider === 'workbuddy_intl' ? 'workbuddy'
    : account.provider === 'qoder' ? (account.region === 'cn' ? 'qoder-cn' : 'qoder') : account.provider
}

export function AccountProbeDialog({ account, models, csrf, request, Modal, onClose }) {
  const prefix = accountModelPrefix(account)
  const options = [...new Set(models.filter(m => m.id?.startsWith(`${prefix}/`) && !m.alias_target).map(m => m.id))]
  const [model, setModel] = useState(options.find(id => /deepseek.*4[.-]?1/i.test(id)) || options[0] || '')
  const [message, setMessage] = useState('请只回复：连接成功')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  const id = useId()
  async function submit(event) {
    event.preventDefault()
    setBusy(true); setResult(null)
    try {
      const value = await request('unified/test', { method: 'POST', csrf, body: {
        model, message, account_provider: prefix === 'qoder-cn' ? 'qoder-cn' : account.provider, account_id: account.id,
      } })
      setResult(value)
    } catch (error) { setResult({ ok: false, error: error.message }) }
    finally { setBusy(false) }
  }
  return createPortal(<Modal title="测试账号调用" subtitle={account.name || account.nickname || account.id} onClose={onClose}>
    <div className="account-probe-scope"><FlaskConical size={17} /><div><strong>仅测试当前账号</strong><p>失败时不切换账号。实际请求会消耗少量额度；账号和模型冷却仍然生效。</p></div></div>
    <form onSubmit={submit} className="account-probe-form">
      <label htmlFor={`${id}-model`}>模型</label>
      <input id={`${id}-model`} list={`${id}-models`} value={model} onChange={e => setModel(e.target.value)}
        placeholder={`${prefix}/模型名称`} required disabled={busy} autoComplete="off" />
      <datalist id={`${id}-models`}>{options.map(value => <option key={value} value={value} />)}</datalist>
      {!options.length && <p className="muted">当前目录未返回模型，可以输入该平台的完整模型 ID。</p>}
      <label htmlFor={`${id}-message`}>测试消息</label>
      <textarea id={`${id}-message`} rows="3" value={message} onChange={e => setMessage(e.target.value)} maxLength="16000" required disabled={busy} />
      <button className="button primary wide" disabled={busy || !model.trim() || !message.trim()}>
        {busy ? <LoaderCircle size={16} className="probe-spinner" /> : <FlaskConical size={16} />}{busy ? '正在测试当前账号…' : '开始测试'}
      </button>
    </form>
    {busy && <p className="muted account-probe-wait" role="status">正在等待该账号响应，最长等待 150 秒。</p>}
    {result && <section className={`account-probe-result ${result.ok ? 'success' : 'error'}`} aria-live="polite" role={result.ok ? 'status' : 'alert'}>
      <header>{result.ok ? <Check size={17} /> : <X size={17} />}<strong>{result.ok ? '调用成功' : '调用失败'}</strong>
        {result.status && <span>HTTP {result.status}</span>}{result.duration_ms != null && <span><Clock size={12} />{result.duration_ms.toLocaleString()} ms</span>}</header>
      <p className="muted">{result.account_confirmed ? '已确认使用当前账号' : '未确认账号完成调用'} · {result.model || model}</p>
      <pre>{result.ok ? result.answer : result.error || '上游未提供错误详情'}</pre>
    </section>}
  </Modal>, document.body)
}
