import React, { useEffect, useState } from 'react'
import { Network, Save, Trash2 } from 'lucide-react'

export function ProxySettingsPanel({ request, csrf }) {
  const [state, setState] = useState(null)
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  async function load() {
    setError('')
    try { setState(await request('unified/proxies')) } catch (e) { setError(e.message) }
  }
  useEffect(() => { load() }, [])
  async function save(event) {
    event.preventDefault()
    setBusy(true); setError(''); setMessage('')
    try {
      setState(await request('unified/proxies', { method: 'PATCH', csrf, body: draft }))
      setDraft({}); setMessage('代理设置已保存，新请求立即生效')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  return <form className="content-card" onSubmit={save}>
    <span className="section-label">网络出口</span>
    <h2><Network size={17} aria-hidden="true" /> 出站代理</h2>
    <p className="muted settings-copy">Antigravity 独立代理只作用于 Google 登录、令牌刷新和模型请求。全局代理作用于所有平台；独立代理优先生效。</p>
    {[
      ['antigravity_proxy', 'Antigravity 独立代理'],
      ['global_proxy', '全局代理'],
    ].map(([field, label]) => <div key={field}>
      <label htmlFor={field}>{label} · {Object.hasOwn(draft, field) ? draft[field] ? '待保存' : '待清除' : state?.settings[field]?.configured ? '已配置' : '未配置'}</label>
      <div className="head-actions">
        <input id={field} type="password" autoComplete="new-password" value={draft[field] ?? ''}
          disabled={!state || busy} placeholder={state?.settings[field]?.configured ? '已加密保存；输入新地址可替换' : 'http://用户名:密码@地址:端口'}
          onChange={e => setDraft(current => ({ ...current, [field]: e.target.value }))} />
        <button type="button" className="button secondary" aria-label={`清除${label}`} disabled={!state || busy}
          onClick={() => setDraft(current => ({ ...current, [field]: '' }))}><Trash2 size={15} /></button>
      </div>
    </div>)}
    <p className="muted settings-copy">Antigravity 当前出口：{state ? { independent: '独立代理', global: '全局代理', direct: '直接连接' }[state.antigravity_source] : '加载中…'}。清除独立代理后跟随全局设置；清除全局代理后其他平台直接连接。</p>
    <button className="button primary" disabled={busy || !state || !Object.keys(draft).length}><Save size={15} />{busy ? '保存中…' : '保存代理设置'}</button>
    {error && <div className="result-banner error" role="alert">{error} {!state && <button className="link-button" type="button" onClick={load}>重试</button>}</div>}
    {message && <div className="result-banner" role="status">{message}</div>}
  </form>
}
