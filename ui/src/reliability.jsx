import React, { useEffect, useState } from 'react'
import {
  ArrowRight,
  Check,
  Copy,
  Pencil,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from 'lucide-react'
import './reliability.css'

export function ModelAliasesPanel({ data, request, csrf, onRefresh }) {
  const [rows, setRows] = useState([])
  const [form, setForm] = useState({ id: '', target: '', name: '' })
  const [editing, setEditing] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const models = (data?.models || []).filter((m) => !m.alias_target)
  const load = () =>
    request('unified/model-aliases').then((r) => setRows(r.aliases))
  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [data])
  async function mutate(task) {
    setBusy(true)
    setError('')
    setMessage('')
    try {
      await task()
      await load()
      await onRefresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  function save(event) {
    event.preventDefault()
    mutate(async () => {
      await request(
        `unified/model-aliases${editing ? '/' + encodeURIComponent(editing) : ''}`,
        {
          method: editing ? 'PATCH' : 'POST',
          csrf,
          body: { ...form, name: form.name || form.id },
        },
      )
      setForm({ id: '', target: '', name: '' })
      setEditing('')
      setMessage('模型别名已保存')
    })
  }
  return (
    <details className="reliability-disclosure">
      <summary>
        模型别名{' '}
        <span className="muted">
          {rows.length} 条映射 · 显示名称与实际 ID 分开管理
        </span>
      </summary>
      <div className="reliability-body">
        <form onSubmit={save} className="reliability-form">
          <label>
            调用时使用的别名 ID
            <input
              required
              aria-label="别名 ID"
              readOnly={!!editing}
              value={form.id}
              placeholder="workbuddy/glm-53-flash"
              onChange={(e) => setForm({ ...form, id: e.target.value })}
            />
          </label>
          <label>
            实际目标模型
            <input
              required
              aria-label="别名目标"
              list="alias-targets"
              value={form.target}
              placeholder="搜索 前缀/模型"
              onChange={(e) => setForm({ ...form, target: e.target.value })}
            />
            <datalist id="alias-targets">
              {models.map((m) => (
                <option key={m.id} value={m.id} />
              ))}
            </datalist>
          </label>
          <label>
            显示名称
            <input
              aria-label="别名显示名称"
              value={form.name}
              placeholder="可选"
              onChange={(e) => setForm({ ...form, name: e.target.value })}
              maxLength={100}
            />
          </label>
          <div className="head-actions">
            <button className="button primary" disabled={busy}>
              <Save size={15} />
              {editing ? '保存修改' : '添加别名'}
            </button>
            {editing && (
              <button
                type="button"
                className="button"
                onClick={() => {
                  setEditing('')
                  setForm({ id: '', target: '', name: '' })
                }}
              >
                取消
              </button>
            )}
          </div>
        </form>
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
        <div className="reliability-list">
          {rows.map((r) => (
            <div key={r.id} className="alias-line">
              <code>{r.id}</code>
              <ArrowRight size={14} />
              <code>{r.target}</code>
              <span className="muted">{r.enabled ? '已启用' : '已暂停'}</span>
              <div className="head-actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={busy}
                  onClick={() => {
                    setEditing(r.id)
                    setForm({ id: r.id, target: r.target, name: r.name })
                  }}
                >
                  编辑
                </button>
                <button
                  type="button"
                  className="link-button"
                  disabled={busy}
                  onClick={() =>
                    mutate(() =>
                      request(
                        'unified/model-aliases/' + encodeURIComponent(r.id),
                        {
                          method: 'PATCH',
                          body: { enabled: !r.enabled },
                          csrf,
                        },
                      ),
                    )
                  }
                >
                  {r.enabled ? '暂停' : '启用'}
                </button>
                <button
                  type="button"
                  className="link-button danger-text"
                  disabled={busy}
                  onClick={() => {
                    if (window.confirm(`删除别名 ${r.id}？`))
                      mutate(() =>
                        request(
                          'unified/model-aliases/' + encodeURIComponent(r.id),
                          { method: 'DELETE', csrf },
                        ),
                      )
                  }}
                >
                  删除
                </button>
              </div>
            </div>
          ))}
        </div>
        <p className="field-help">
          别名只指向真实模型或路由，不会按相似名称自动换模型。密钥须同时具有别名和目标的访问权限。
        </p>
      </div>
    </details>
  )
}
