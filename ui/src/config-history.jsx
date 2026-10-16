import React, { useCallback, useEffect, useRef, useState } from 'react'
import { ArrowRight, History, RefreshCw, RotateCcw } from 'lucide-react'
import './reliability.css'

const kinds = {
  route: '智能路由',
  connection: '自定义服务',
  key: '密钥名称与规则',
  alias: '模型别名',
  automation: '自动任务',
  network: '响应超时',
  probe_plans: '巡检计划',
  credit_policy: '临期额度',
  capability: '模型能力',
}
const text = (v) =>
  v === null || v === undefined
    ? '未设置'
    : typeof v === 'string'
      ? v
      : JSON.stringify(v)

function Diff({ rows }) {
  return (
    <div className="config-diff">
      {rows.length ? (
        rows.map((r, i) => (
          <div key={i}>
            <strong>{r.path}</strong>
            <div>
              <code className="diff-before">{text(r.before)}</code>
              <ArrowRight size={15} />
              <code className="diff-after">{text(r.after)}</code>
            </div>
          </div>
        ))
      ) : (
        <p className="muted">没有差异</p>
      )}
    </div>
  )
}

export function ConfigHistoryPage({
  request,
  csrf,
  DataTable,
  Modal,
  onRefresh,
}) {
  const [state, setState] = useState({ items: [], total: 0 }),
    [kind, setKind] = useState(''),
    [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState(null),
    [side, setSide] = useState('before'),
    [detail, setDetail] = useState(null)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [message, setMessage] = useState('')
  const previewSequence = useRef(0)
  const load = useCallback(
    async () =>
      setState(
        await request(
          `unified/config-history?limit=30&offset=${offset}&kind=${encodeURIComponent(kind)}`,
        ),
      ),
    [kind, offset],
  )
  useEffect(() => {
    load().catch((e) => setError(e.message))
  }, [load])
  async function preview(row) {
    const sequence = ++previewSequence.current
    setSelected(row)
    setSide('before')
    setDetail(null)
    setError('')
    try {
      const value = await request('unified/config-history/' + row.id)
      if (sequence === previewSequence.current) setDetail(value)
    } catch (e) {
      if (sequence === previewSequence.current) setError(e.message)
    }
  }
  async function restore() {
    if (
      !window.confirm(
        `只恢复「${kinds[selected.kind]} / ${selected.entity}」到本次修改${side === 'before' ? '前' : '后'}的配置？`,
      )
    )
      return
    setBusy(true)
    setError('')
    try {
      await request(`unified/config-history/${selected.id}/restore`, {
        method: 'POST',
        csrf,
        body: { fingerprint: detail.fingerprint, side },
      })
      setSelected(null)
      setMessage('单项配置已恢复')
      await load()
      await onRefresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }
  const restoreRows =
    side === 'before' ? detail?.restore_changes : detail?.restore_after_changes
  return (
    <section className="content-card">
      <div className="section-head">
        <div>
          <span className="section-label">配置版本</span>
          <h2>每次修改都有记录</h2>
          <p className="muted">
            保留最近 90 天、最多 500
            次修改。服务凭据加密保存，差异只显示隐藏标记。
          </p>
        </div>
        <button
          className="button secondary"
          onClick={() => load().catch((e) => setError(e.message))}
        >
          <RefreshCw size={15} />
          刷新
        </button>
      </div>
      <div className="toolbar">
        <label className="history-filter">
          配置类型
          <select
            aria-label="配置历史类型"
            value={kind}
            onChange={(e) => {
              setKind(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">全部</option>
            {Object.entries(kinds).map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{state.total} 条修改</span>
      </div>
      {!selected && error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="success-text" role="status">
          {message}
        </p>
      )}
      <DataTable
        columns={['时间', '配置', '操作', '详情']}
        empty={
          !state.items.length && '修改路由、服务或规则后，版本会自动出现在这里'
        }
      >
        {state.items.map((row) => (
          <tr key={row.id}>
            <td className="muted">
              {new Date(row.created * 1000).toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
              })}
            </td>
            <td>
              <strong>{kinds[row.kind] || row.kind}</strong>
              <small>
                <code>{row.entity}</code>
              </small>
            </td>
            <td>
              {row.state === 'pending'
                ? '修改未完整结束'
                : row.source.startsWith('restore:')
                  ? '恢复配置'
                  : '编辑配置'}
            </td>
            <td>
              <button className="link-button" onClick={() => preview(row)}>
                查看差异
              </button>
            </td>
          </tr>
        ))}
      </DataTable>
      {state.total > 30 && (
        <div className="head-actions history-pagination">
          <button
            className="button secondary"
            disabled={!offset}
            onClick={() => setOffset(Math.max(0, offset - 30))}
          >
            上一页
          </button>
          <span className="muted">
            {Math.floor(offset / 30) + 1} / {Math.ceil(state.total / 30)}
          </span>
          <button
            className="button secondary"
            disabled={offset + 30 >= state.total}
            onClick={() => setOffset(offset + 30)}
          >
            下一页
          </button>
        </div>
      )}
      {selected && (
        <Modal
          title={`${kinds[selected.kind] || selected.kind} · 版本 ${selected.id}`}
          subtitle={selected.entity}
          onClose={() => {
            if (!busy) {
              previewSequence.current++
              setSelected(null)
              setError('')
            }
          }}
        >
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          {!detail && !error && <p className="muted">正在读取差异…</p>}
          {detail && (
            <>
              <h3 className="control-subtitle">这次修改</h3>
              <Diff rows={detail.changes} />
              <div className="history-restore-heading">
                <h3 className="control-subtitle">恢复预览</h3>
                <select
                  aria-label="恢复到哪个版本"
                  value={side}
                  onChange={(e) => setSide(e.target.value)}
                >
                  <option value="before">恢复到修改前</option>
                  {detail.can_restore_after && (
                    <option value="after">恢复到修改后</option>
                  )}
                </select>
              </div>
              <Diff rows={restoreRows || []} />
              <p className="field-help">
                只恢复此项配置；余额、调用记录和其他设置保留。恢复本身也会生成一条版本记录。
              </p>
              <div className="head-actions">
                <button
                  className="button secondary"
                  disabled={busy}
                  onClick={() => preview(selected)}
                >
                  <RefreshCw size={14} />
                  重新预览
                </button>
                <button
                  className="button primary"
                  disabled={busy || !restoreRows?.length}
                  onClick={restore}
                >
                  <RotateCcw size={15} />
                  {busy ? '正在恢复…' : '恢复此项配置'}
                </button>
              </div>
            </>
          )}
        </Modal>
      )}
    </section>
  )
}
