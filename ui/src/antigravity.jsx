import React, { useId, useState } from 'react'
import { dateText } from './record-format'
import './antigravity.css'

export function AntigravityCallback({ flow, request, csrf, onSaved }) {
  const id = useId()
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function finish() {
    setBusy(true)
    setError('')
    try {
      await request(`unified/antigravity/login/${flow.id}/callback`, {
        method: 'POST', body: { url: url.trim() }, csrf,
      })
      setUrl('')
      await onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="antigravity-callback">
      <label htmlFor={id}>手动完成授权</label>
      <p className="field-help">
        {flow.manual ? '远程部署时，授权后 localhost 页面可能无法打开。' : '如果账号没有自动添加，'}
        复制浏览器地址栏中的完整回调地址并粘贴到这里。
      </p>
      <input id={id} autoComplete="off" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://localhost:…/callback?state=…&code=…" />
      <button className="button secondary wide" disabled={busy || !url.trim()} onClick={finish}>
        {busy ? '正在保存…' : '完成授权并添加'}
      </button>
      {error && <p className="form-error" role="alert">{error}</p>}
    </div>
  )
}

export function AntigravityQuota({ value }) {
  const windows = value.quota || []
  return (
    <div className="antigravity-quota">
      <p className="field-help">每个模型组的周期额度分别计算，重置后恢复；百分比不会换算成平台积分。</p>
      {windows.length ? windows.map((window, index) => (
        <section className="antigravity-window" key={index}>
          <div className="antigravity-window-heading">
            <strong>{window.name}</strong>
            <span className="tabular">剩余 {window.remaining}%</span>
          </div>
          <progress max="100" value={window.remaining} aria-label={`${window.name} 剩余额度`} />
          {window.reset_at && <p className="field-help">{dateText(window.reset_at)} 重置</p>}
        </section>
      )) : <p className="subtle-note">上游尚未提供周期额度信息。</p>}
    </div>
  )
}
