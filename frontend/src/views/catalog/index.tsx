import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

const ENDPOINT = '/api/catalog'
const columns = [
  '条目编号',
  '数据集名称',
  '业务域',
  '字段清单',
  '更新频率',
  '数据等级',
  '负责人',
  '条目状态',
]

type Row = Record<string, string | number | null>
type Issue = { 类型: string; 级别: string; 说明: string }
type Precheck = {
  条目编号: string
  阻断发布: boolean
  问题清单: Issue[]
  条目登记等级: string
  建议发布等级: string
  生效字段版本: number | null
  字段数: number
}
type Plan = {
  id: number
  条目编号: string
  数据集名称: string
  业务域: string
  更新频率: string
  数据等级: string
  负责人: string
  字段版本: number
  字段快照: Array<Record<string, string>>
  发布操作人: string
  发布时间: string
  发布状态: string
  等级冲突说明: string[]
}
type Version = {
  id: number
  条目编号: string
  版本号: number
  active: boolean
  字段清单: Array<Record<string, string>>
  更新人: string
  更新时间: string
  备注: string
}
type History = {
  id: number
  条目编号: string
  操作类型: string
  操作人: string
  操作时间: string
  字段版本: number | null
  发布等级: string | null
  说明: string
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

export default function CatalogView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [okMessage, setOkMessage] = useState('')
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [tab, setTab] = useState<'entries' | 'plans' | 'history'>('entries')
  const [operator, setOperator] = useState('张晓雯')
  const filterFields = useMemo(() => columns.slice(0, 3), [])

  const [precheck, setPrecheck] = useState<Precheck | null>(null)
  const [versionEntry, setVersionEntry] = useState<{ code: string; versions: Version[] } | null>(null)
  const [snapshotPlan, setSnapshotPlan] = useState<Plan | null>(null)
  const [ownerTarget, setOwnerTarget] = useState<string>('')
  const [newOwner, setNewOwner] = useState('')
  const [plans, setPlans] = useState<Plan[]>([])
  const [histories, setHistories] = useState<History[]>([])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const query = new URLSearchParams(filters).toString()
    try {
      const response = await request(`${ENDPOINT}?${query}`)
      if (!response.ok) throw new Error('目录条目列表读取失败')
      const payload = await response.json()
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据目录检索列表读取失败')
    }
  }, [filters])

  const reloadPlans = useCallback(async () => {
    const response = await request(`${ENDPOINT}/publish/plans?include_offline=true`)
    if (response.ok) {
      const payload = await response.json()
      setPlans(payload.items ?? [])
    }
  }, [])

  const reloadHistory = useCallback(async () => {
    const response = await request(`${ENDPOINT}/publish/history`)
    if (response.ok) {
      const payload = await response.json()
      setHistories(payload.items ?? [])
    }
  }, [])

  useEffect(() => {
    void reload()
  }, [reload])

  useEffect(() => {
    if (tab === 'plans') void reloadPlans()
    if (tab === 'history') void reloadHistory()
  }, [tab, reloadPlans, reloadHistory])

  function flash(ok: boolean, message: string) {
    if (ok) {
      setOkMessage(message)
      setErrorMessage('')
    } else {
      setErrorMessage(message)
      setOkMessage('')
    }
  }

  async function runPrecheck(code: string) {
    const response = await request(`${ENDPOINT}/entry/${code}/precheck`)
    if (!response.ok) {
      flash(false, errorDetail(await response.json().catch(() => ({}))))
      return
    }
    setPrecheck((await response.json()) as Precheck)
  }

  async function runPublish(code: string) {
    const result = await postJson(`${ENDPOINT}/entry/${code}/publish`, { values: { operator } })
    flash(result.ok, result.ok ? (result.data as { message: string }).message : errorDetail(result.data))
    if (result.ok) {
      await reload()
      await reloadPlans()
      await reloadHistory()
    }
  }

  async function openVersions(code: string) {
    const response = await request(`${ENDPOINT}/entry/${code}/field-versions`)
    if (!response.ok) {
      flash(false, errorDetail(await response.json().catch(() => ({}))))
      return
    }
    const payload = (await response.json()) as { items: Version[] }
    setVersionEntry({ code, versions: payload.items })
  }

  async function submitOwner() {
    if (!ownerTarget) return
    const result = await postJson(`${ENDPOINT}/entry/${ownerTarget}/owner`, {
      values: { operator, new_owner: newOwner },
    })
    flash(result.ok, result.ok ? (result.data as { message: string }).message : errorDetail(result.data))
    if (result.ok) {
      setOwnerTarget('')
      setNewOwner('')
      await reload()
      await reloadHistory()
    }
  }

  async function runLegacyAction(action: string, row: Row) {
    const result = await postJson(`${ENDPOINT}/${row.id}/actions`, { values: { action } })
    flash(result.ok, result.ok ? (result.data as { message: string }).message : errorDetail(result.data))
    if (result.ok) await reload()
  }

  return (
    <section className="page" data-module="catalog">
      <header className="page-head">
        <div>
          <h2>数据目录发布管理</h2>
          <p className="page-desc">
            按字段清单、负责人、数据等级与更新频率生成可发布的目录清单；发布前解释字段缺失、负责人缺失与等级冲突，
            采样侧共享同一份只读字段快照。
          </p>
        </div>
        <div className="page-actions">
          <label className="form-item" style={{ minWidth: 200 }}>
            <span>当前操作人（用于负责人归属校验）</span>
            <input value={operator} onChange={(event) => setOperator(event.target.value)} placeholder="输入操作人姓名" />
          </label>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出数据目录清单
          </button>
        </div>
      </header>

      <div className="tabs">
        <button className={`tab ${tab === 'entries' ? 'active' : ''}`} type="button" onClick={() => setTab('entries')}>目录条目与发布</button>
        <button className={`tab ${tab === 'plans' ? 'active' : ''}`} type="button" onClick={() => setTab('plans')}>发布方案清单（{plans.length}）</button>
        <button className={`tab ${tab === 'history' ? 'active' : ''}`} type="button" onClick={() => setTab('history')}>发布历史（{histories.length}）</button>
      </div>

      {errorMessage && <p className="error-text">{errorMessage}</p>}
      {okMessage && <p className="success-text">{okMessage}</p>}

      {tab === 'entries' && (
        <>
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
                  <th>发布操作</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={String(row.id)}>
                    {columns.map((column) => <td key={column}>{row[column] === '' ? <span className="small-text">未填写</span> : (row[column] ?? '—')}</td>)}
                    <td className="row-actions">
                      <button className="link" type="button" onClick={() => void runPrecheck(String(row.条目编号))}>发布前校验</button>
                      <button className="link" type="button" onClick={() => void openVersions(String(row.条目编号))}>字段版本</button>
                      <button className="link" type="button" onClick={() => void runPublish(String(row.条目编号))}>生成发布方案</button>
                      <button className="link" type="button" onClick={() => { setOwnerTarget(String(row.条目编号)); setNewOwner('') }}>调整负责人</button>
                      <button className="link" type="button" onClick={() => void runLegacyAction('下线条目', row)}>下线</button>
                    </td>
                  </tr>
                ))}
                {rows.length === 0 && (
                  <tr><td className="empty-state" colSpan={columns.length + 1}>暂无目录条目</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'plans' && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>条目编号</th><th>数据集名称</th><th>业务域</th><th>更新频率</th>
                <th>发布等级</th><th>负责人</th><th>字段版本</th><th>字段数</th>
                <th>发布操作人</th><th>发布时间</th><th>等级冲突说明</th><th>快照</th>
              </tr>
            </thead>
            <tbody>
              {plans.map((plan) => (
                <tr key={plan.id}>
                  <td>{plan.条目编号}</td>
                  <td>{plan.数据集名称}</td>
                  <td>{plan.业务域}</td>
                  <td>{plan.更新频率}</td>
                  <td><span className="badge info">{plan.数据等级}</span></td>
                  <td>{plan.负责人}</td>
                  <td>v{plan.字段版本}</td>
                  <td>{plan.字段快照.length}</td>
                  <td>{plan.发布操作人}</td>
                  <td>{plan.发布时间}</td>
                  <td>{plan.等级冲突说明.length ? plan.等级冲突说明.join('；') : '—'}</td>
                  <td><button className="link" type="button" onClick={() => setSnapshotPlan(plan)}>查看只读快照</button></td>
                </tr>
              ))}
              {plans.length === 0 && <tr><td className="empty-state" colSpan={12}>暂无发布方案，请先在条目页生成</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'history' && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>条目编号</th><th>操作类型</th><th>操作人</th><th>操作时间</th><th>字段版本</th><th>发布等级</th><th>说明</th></tr>
            </thead>
            <tbody>
              {histories.map((item) => (
                <tr key={item.id}>
                  <td>{item.条目编号}</td>
                  <td><span className={`badge ${item.操作类型 === '负责人调整' ? 'warn' : 'ok'}`}>{item.操作类型}</span></td>
                  <td>{item.操作人}</td>
                  <td>{item.操作时间}</td>
                  <td>{item.字段版本 != null ? `v${item.字段版本}` : '—'}</td>
                  <td>{item.发布等级 ?? '—'}</td>
                  <td>{item.说明}</td>
                </tr>
              ))}
              {histories.length === 0 && <tr><td className="empty-state" colSpan={7}>暂无发布历史</td></tr>}
            </tbody>
          </table>
        </div>
      )}

      <footer className="page-foot">
        <span>共 {total} 条目录条目；重复发布同一条目只保留一份方案，并发更新字段清单仅一个版本生效</span>
      </footer>

      {precheck && (
        <div className="modal-mask" onClick={() => setPrecheck(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>发布前校验 · {precheck.条目编号}</h3>
              <button className="btn ghost" type="button" onClick={() => setPrecheck(null)}>关闭</button>
            </div>
            <p>
              条目登记等级 <strong>{precheck.条目登记等级 || '—'}</strong>，
              建议发布等级 <strong>{precheck.建议发布等级 || '—'}</strong>，
              生效字段版本 {precheck.生效字段版本 != null ? `v${precheck.生效字段版本}` : '无'}，字段数 {precheck.字段数}
            </p>
            <p>
              校验结论：
              {precheck.阻断发布
                ? <span className="badge block">存在阻断项，暂不可发布</span>
                : <span className="badge ok">无阻断项，可以发布</span>}
            </p>
            {precheck.问题清单.length === 0 ? (
              <p className="small-text">字段缺失、负责人缺失、等级冲突均未发现。</p>
            ) : (
              <ul className="issue-list">
                {precheck.问题清单.map((issue, index) => (
                  <li key={index}>
                    <span className={`badge ${issue.级别 === '阻断' ? 'block' : 'warn'}`}>{issue.类型}·{issue.级别}</span>
                    <div>{issue.说明}</div>
                  </li>
                ))}
              </ul>
            )}
            <div className="modal-actions">
              <button className="btn primary" type="button" disabled={precheck.阻断发布} onClick={() => { setPrecheck(null); void runPublish(precheck.条目编号) }}>
                {precheck.阻断发布 ? '存在阻断项，不能发布' : '确认生成发布方案'}
              </button>
            </div>
          </div>
        </div>
      )}

      {versionEntry && (
        <VersionModal
          entry={versionEntry}
          operator={operator}
          onClose={() => setVersionEntry(null)}
          onChanged={async () => {
            await openVersions(versionEntry.code)
            await reload()
          }}
          onError={(message) => flash(false, message)}
        />
      )}

      {snapshotPlan && (
        <div className="modal-mask" onClick={() => setSnapshotPlan(null)}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>只读字段快照 · {snapshotPlan.条目编号}（v{snapshotPlan.字段版本}）</h3>
              <button className="btn ghost" type="button" onClick={() => setSnapshotPlan(null)}>关闭</button>
            </div>
            <div className="readonly-note">该快照在发布时冻结，数据采样管理仅可只读引用，任何修改都不会回写到目录侧。</div>
            <table className="data-table">
              <thead><tr><th>字段名</th><th>字段类型</th><th>数据等级</th><th>字段说明</th></tr></thead>
              <tbody>
                {snapshotPlan.字段快照.map((field, index) => (
                  <tr key={index}>
                    <td>{field.字段名}</td><td>{field.字段类型}</td><td>{field.数据等级}</td><td>{field.字段说明}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {ownerTarget && (
        <div className="modal-mask" onClick={() => setOwnerTarget('')}>
          <div className="modal" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <h3>调整负责人 · {ownerTarget}</h3>
              <button className="btn ghost" type="button" onClick={() => setOwnerTarget('')}>取消</button>
            </div>
            <p className="small-text">仅平台管理员或现任负责人可调整；调整后旧发布记录仍保留原操作人。CATA-0001 如非必要请保持原负责人。</p>
            <label className="form-item">
              <span>新负责人姓名</span>
              <input value={newOwner} onChange={(event) => setNewOwner(event.target.value)} placeholder="例如：王梓涵" />
            </label>
            <div className="modal-actions">
              <button className="btn primary" type="button" onClick={() => void submitOwner()}>确认调整</button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function VersionModal(props: {
  entry: { code: string; versions: Version[] }
  operator: string
  onClose: () => void
  onChanged: () => Promise<void>
  onError: (message: string) => void
}) {
  const { entry, operator } = props
  const active = entry.versions.find((item) => item.active)
  const [draft, setDraft] = useState(JSON.stringify(active?.字段清单 ?? [], null, 2))
  const [remark, setRemark] = useState('')

  async function submit() {
    let parsed: unknown
    try {
      parsed = JSON.parse(draft)
    } catch {
      props.onError('字段清单必须是 JSON 数组，例如 [{"字段名":"客户编号","数据等级":"L2"}]')
      return
    }
    const result = await postJson(`${ENDPOINT}/entry/${entry.code}/field-versions`, {
      values: {
        operator,
        expected_version: active?.版本号,
        remark,
        字段清单: parsed,
      },
    })
    if (!result.ok) {
      props.onError(errorDetail(result.data))
      return
    }
    await props.onChanged()
  }

  return (
    <div className="modal-mask" onClick={props.onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <div className="modal-head">
          <h3>字段清单版本 · {entry.code}（当前生效 v{active?.版本号 ?? '—'}）</h3>
          <button className="btn ghost" type="button" onClick={props.onClose}>关闭</button>
        </div>
        <table className="data-table" style={{ marginBottom: 12 }}>
          <thead><tr><th>版本</th><th>状态</th><th>更新人</th><th>更新时间</th><th>备注</th></tr></thead>
          <tbody>
            {entry.versions.map((version) => (
              <tr key={version.id}>
                <td>v{version.版本号}</td>
                <td>{version.active ? <span className="badge ok">生效中</span> : <span className="badge info">历史版本</span>}</td>
                <td>{version.更新人}</td>
                <td>{version.更新时间}</td>
                <td>{version.备注}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="small-text">
          只有条目负责人可以提交新版本；提交时携带当前生效版本号，并发场景下后提交者会收到 409 版本冲突，保证只有一个版本生效。
        </p>
        <div className="form-grid">
          <label className="form-item" style={{ gridColumn: '1 / -1' }}>
            <span>新字段清单（JSON 数组，字段名/字段类型/数据等级/字段说明）</span>
            <textarea rows={8} value={draft} onChange={(event) => setDraft(event.target.value)} />
          </label>
          <label className="form-item">
            <span>版本备注</span>
            <input value={remark} onChange={(event) => setRemark(event.target.value)} placeholder="例如：补齐客单价字段" />
          </label>
        </div>
        <div className="modal-actions">
          <button className="btn primary" type="button" onClick={() => void submit()}>提交新版本（基于 v{active?.版本号}）</button>
        </div>
      </div>
    </div>
  )
}
