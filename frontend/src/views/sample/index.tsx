import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/sample'
const columns = [
  '配置编号',
  '数据源名称',
  '采样算法',
  '样本比例',
  '随机种子',
  '分层字段',
  '输出表名',
  '配置状态',
]

type Row = Record<string, string | number | null>
type SnapshotField = Record<string, string>
type Snapshot = {
  配置编号: string
  已引用: boolean
  引用条目编号?: string
  引用字段版本?: string
  引用性质?: string
  只读?: boolean
  提示?: string
  字段快照: SnapshotField[]
}

async function postJson(path: string, body: unknown): Promise<{ ok: boolean; status: number; data: unknown }> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  const data = await response.json().catch(() => ({}))
  return { ok: response.ok, status: response.status, data }
}

function errorDetail(data: unknown): string {
  if (data && typeof data === 'object' && 'detail' in data) return String((data as { detail: unknown }).detail)
  if (data && typeof data === 'object' && 'message' in data) return String((data as { message: unknown }).message)
  return '操作未生效，请稍后重试'
}

export default function SampleView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [okMessage, setOkMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const [bindTarget, setBindTarget] = useState<Row | null>(null)
  const [entryCode, setEntryCode] = useState('')
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('采样配置列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据采样管理列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  function flash(ok: boolean, message: string) {
    if (ok) {
      setOkMessage(message)
      setErrorMessage('')
    } else {
      setErrorMessage(message)
      setOkMessage('')
    }
  }

  async function runAction(action: string, row: Row) {
    const result = await postJson(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    flash(result.ok, result.ok ? (result.data as { message: string }).message : errorDetail(result.data))
    if (result.ok) await reload()
  }

  async function submitBind() {
    if (!bindTarget) return
    const result = await postJson(`${ENDPOINT}/${bindTarget.id}/bind-catalog`, {
      values: { 条目编号: entryCode.trim() },
    })
    flash(result.ok, result.ok ? (result.data as { message: string }).message : errorDetail(result.data))
    if (result.ok) {
      setBindTarget(null)
      setEntryCode('')
      await reload()
    }
  }

  async function openSnapshot(row: Row) {
    const response = await request(`${ENDPOINT}/${row.id}/catalog-snapshot`)
    if (!response.ok) {
      flash(false, errorDetail(await response.json().catch(() => ({}))))
      return
    }
    setSnapshot((await response.json()) as Snapshot)
  }

  return (
    <section className="page" data-module="sample">
      <header className="page-head">
        <div>
          <h2>数据采样管理</h2>
          <p className="page-desc">
            采样输出表必须绑定目录发布方案冻结的同一份字段清单快照；采样侧只有只读权限，
            已下线数据集仅保留历史引用。
          </p>
        </div>
        <div className="page-actions">
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出采样配置清单
          </button>
        </div>
      </header>

      {errorMessage && <p className="error-text">{errorMessage}</p>}
      {okMessage && <p className="success-text">{okMessage}</p>}

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((column) => <th key={column}>{column}</th>)}
              <th>引用目录条目</th>
              <th>字段版本</th>
              <th>引用性质</th>
              <th>快照与动作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.id)}>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td>{row.引用条目编号 ? String(row.引用条目编号) : <span className="small-text">未绑定</span>}</td>
                <td>{row.引用字段版本 ? `v${String(row.引用字段版本)}` : '—'}</td>
                <td>
                  {row.引用性质
                    ? <span className={`badge ${row.引用性质 === '历史引用' ? 'warn' : 'info'}`}>{String(row.引用性质)}</span>
                    : '—'}
                </td>
                <td className="row-actions">
                  <button className="link" type="button" onClick={() => void openSnapshot(row)}>只读快照</button>
                  <button
                    className="link"
                    type="button"
                    onClick={() => { setBindTarget(row); setEntryCode(String(row.引用条目编号 ?? '')) }}
                  >
                    {row.引用条目编号 ? '重新绑定' : '绑定发布方案'}
                  </button>
                  <button className="link" type="button" onClick={() => void runAction('启用采样', row)}>启用采样</button>
                  <button className="link" type="button" onClick={() => void runAction('暂停采样', row)}>暂停采样</button>
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td className="empty-state" colSpan={columns.length + 4}>暂无采样配置数据</td></tr>
            )}
          </tbody>
        </table>
      </div>

      <footer className="page-foot">
        <span>共 {total} 条采样配置；只读快照来自目录发布方案，字段清单的编辑权限仅属于条目负责人</span>
      </footer>

      {bindTarget && (
        <div className="modal-mask" onClick={() => setBindTarget(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>绑定目录发布方案 · {String(bindTarget.配置编号)}</h3>
              <button className="btn ghost" type="button" onClick={() => setBindTarget(null)}>取消</button>
            </div>
            <div className="readonly-note">
              绑定后采样输出表将冻结该条目发布方案的字段快照（只读）；已下线条目（如 CATA-0003）不能新增绑定，只保留历史引用。
            </div>
            <label className="form-item">
              <span>目录条目编号</span>
              <input value={entryCode} onChange={(event) => setEntryCode(event.target.value)} placeholder="例如：CATA-0001" />
            </label>
            <div className="modal-actions">
              <button className="btn primary" type="button" onClick={() => void submitBind()}>确认绑定并冻结快照</button>
            </div>
          </div>
        </div>
      )}

      {snapshot && (
        <div className="modal-mask" onClick={() => setSnapshot(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>只读字段快照 · {snapshot.配置编号}</h3>
              <button className="btn ghost" type="button" onClick={() => setSnapshot(null)}>关闭</button>
            </div>
            {!snapshot.已引用 ? (
              <p className="small-text">{snapshot.提示 ?? '该采样配置尚未绑定目录发布方案'}</p>
            ) : (
              <>
                <div className="readonly-note">
                  引用条目 {snapshot.引用条目编号} · 字段版本 v{snapshot.引用字段版本} · {snapshot.引用性质} ·
                  只读共享版本，采样侧不可修改
                </div>
                <table className="data-table">
                  <thead><tr><th>字段名</th><th>字段类型</th><th>数据等级</th><th>字段说明</th></tr></thead>
                  <tbody>
                    {snapshot.字段快照.map((field, index) => (
                      <tr key={index}>
                        <td>{field.字段名}</td>
                        <td>{field.字段类型}</td>
                        <td>{field.数据等级}</td>
                        <td>{field.字段说明}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
