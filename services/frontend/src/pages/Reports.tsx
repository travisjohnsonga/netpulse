import { useState, type ReactNode } from 'react'
import clsx from 'clsx'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  generateReport, fetchReports, downloadReport, deleteReport, bulkDeleteReports,
  fetchReportSchedules, createReportSchedule, deleteReportSchedule,
  type GeneratedReportRow, type ReportScheduleRow,
} from '../api/client'
import { usePreferencesStore } from '../store/preferencesStore'

type Endpoint = 'compliance-summary' | 'daily-ops'

interface ReportDef {
  endpoint: Endpoint
  icon: string
  title: string
  blurb: string
  formats: string[]
  hasGroupBy?: boolean
  hasDate?: boolean
}

const REPORTS: ReportDef[] = [
  {
    endpoint: 'compliance-summary', icon: '📊', title: 'Compliance Summary',
    blurb: 'Fleet compliance by site / role / platform with findings detail and startup-config risk.',
    formats: ['pdf', 'csv', 'json'], hasGroupBy: true,
  },
  {
    endpoint: 'daily-ops', icon: '📋', title: 'Daily Operations Report',
    blurb: 'Security events, device availability, config changes, collection & agent health.',
    formats: ['pdf', 'csv', 'html'], hasDate: true,
  },
]

const DOW_PLURAL = ['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays']

/** Human cadence for a schedule, e.g. "Weekly · Mondays @ 19:00 America/Chicago".
 *  hour/day_* are already in the user's tz (converted by the backend). */
function formatCadence(s: ReportScheduleRow, fallbackTz: string): string {
  const at = `@ ${String(s.hour).padStart(2, '0')}:00 ${s.timezone ?? fallbackTz}`
  switch (s.frequency) {
    case 'weekly':
      return `Weekly · ${DOW_PLURAL[s.day_of_week] ?? `day ${s.day_of_week}`} ${at}`
    case 'monthly':
      return `Monthly · day ${s.day_of_month} ${at}`
    case 'quarterly':
      return `Quarterly · day ${s.day_of_month} (Jan/Apr/Jul/Oct) ${at}`
    default:
      return `Daily ${at}`
  }
}

function fmtBytes(n: number | null): string {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function Reports() {
  const qc = useQueryClient()
  const [modal, setModal] = useState<{ def: ReportDef; mode: 'generate' | 'schedule' | 'preview' } | null>(null)
  const reportsQ = useQuery({ queryKey: ['reports'], queryFn: fetchReports })

  const close = () => setModal(null)
  const onDone = () => { close(); qc.invalidateQueries({ queryKey: ['reports'] }) }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Reports</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">Generate &amp; download on-demand reports, or schedule recurring delivery (email or store-only).</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {REPORTS.map((def) => (
          <div key={def.endpoint} className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-5">
            <div className="text-2xl">{def.icon}</div>
            <h2 className="mt-2 font-semibold text-gray-900 dark:text-gray-100">{def.title}</h2>
            <p className="mt-1 text-sm text-gray-500 dark:text-gray-400">{def.blurb}</p>
            <div className="mt-4 flex gap-2">
              <button onClick={() => setModal({ def, mode: 'generate' })}
                className="px-3 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Generate Now</button>
              <button onClick={() => setModal({ def, mode: 'preview' })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700">Preview</button>
              <button onClick={() => setModal({ def, mode: 'schedule' })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700">Schedule</button>
            </div>
          </div>
        ))}
      </div>

      <RecentReports rows={reportsQ.data ?? []} loading={reportsQ.isLoading} />

      {modal && modal.mode === 'generate' && <GenerateModal def={modal.def} onClose={close} onDone={onDone} />}
      {modal && modal.mode === 'schedule' && <ScheduleModal def={modal.def} onClose={close} onDone={onDone} />}
      {modal && modal.mode === 'preview' && <PreviewModal def={modal.def} onClose={close} />}
    </div>
  )
}

// ── preview (in-browser, before download) ────────────────────────────────────
interface ConfigChange {
  hostname: string
  detected_at: string
  previous_backup_at: string | null
  lines_added: number
  lines_removed: number
  diff_summary: string
  diff: string
}

function DiffView({ diff }: { diff: string }) {
  return (
    <pre className="text-xs font-mono border border-gray-200 dark:border-gray-700 rounded overflow-x-auto max-h-72">
      {diff.split('\n').map((line, i) => {
        const add = line.startsWith('+') && !line.startsWith('+++')
        const rem = line.startsWith('-') && !line.startsWith('---')
        const hunk = line.startsWith('@@')
        return (
          <div key={i} className={
            add ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300 px-2'
              : rem ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300 px-2'
                : hunk ? 'text-blue-600 dark:text-blue-400 px-2'
                  : 'text-gray-500 dark:text-gray-400 px-2'
          }>{line || ' '}</div>
        )
      })}
    </pre>
  )
}

function ConfigChangeRow({ c }: { c: ConfigChange }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="border border-gray-200 dark:border-gray-700 rounded">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-3 px-3 py-2 text-left text-sm hover:bg-gray-50 dark:hover:bg-gray-700/50">
        <span className="font-mono text-gray-900 dark:text-gray-100">{c.hostname}</span>
        <span className="text-green-600 dark:text-green-400">↑{c.lines_added}</span>
        <span className="text-red-600 dark:text-red-400">↓{c.lines_removed}</span>
        <span className="text-gray-400">{(c.detected_at || '').slice(11, 16)}</span>
        <span className="ml-auto text-xs text-blue-600 dark:text-blue-400">{open ? 'Hide diff ▴' : 'Show diff ▾'}</span>
      </button>
      {open && <div className="px-3 pb-3"><DiffView diff={c.diff || '(no diff)'} /></div>}
    </div>
  )
}

function PreviewModal({ def, onClose }: { def: ReportDef; onClose: () => void }) {
  const q = useQuery({
    queryKey: ['report-preview', def.endpoint],
    queryFn: () => generateReport(def.endpoint, { format: 'json' }) as Promise<Record<string, unknown>>,
  })
  const data = q.data as Record<string, unknown> | undefined
  const changes = (data?.config_changes as ConfigChange[]) || []
  const sec = data?.security_events as { total_failures?: number } | undefined
  const svc = data?.service_checks as { total_failures?: number; configured?: boolean } | undefined
  const ce = data?.compliance_events as { fleet_avg_today?: number | null; fleet_grade?: string | null; total_failing_devices?: number } | undefined
  const av = data?.device_availability as { availability_pct?: number; total_outages?: number } | undefined
  const ch = data?.collection_health as { successful?: number; total_attempts?: number } | undefined

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-full max-w-3xl max-h-[85vh] overflow-y-auto p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold text-gray-900 dark:text-gray-100">Preview — {def.title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        {q.isLoading ? (
          <div className="py-10 text-center text-sm text-gray-400">Building preview…</div>
        ) : !data ? (
          <div className="py-10 text-center text-sm text-red-600">Failed to build preview.</div>
        ) : def.endpoint === 'daily-ops' ? (
          <div className="space-y-4">
            <div className="grid grid-cols-5 gap-3 text-sm">
              <Stat label="Device auth failures" value={sec?.total_failures ?? 0} />
              <Stat label="Check failures" value={svc?.configured ? (svc?.total_failures ?? 0) : '—'} />
              <Stat label="Compliance" value={ce?.fleet_avg_today != null ? `${ce.fleet_avg_today} (${ce.fleet_grade ?? '—'})` : '—'} />
              <Stat label="Availability" value={`${av?.availability_pct ?? 100}%`} />
              <Stat label="Collection" value={`${ch?.successful ?? 0}/${ch?.total_attempts ?? 0}`} />
            </div>
            <div>
              <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">Config Changes ({changes.length} device{changes.length === 1 ? '' : 's'})</h4>
              {changes.length === 0 ? (
                <p className="text-sm text-gray-400">No config changes in this period.</p>
              ) : (
                <div className="space-y-2">{changes.map((c, i) => <ConfigChangeRow key={i} c={c} />)}</div>
              )}
            </div>
          </div>
        ) : def.endpoint === 'compliance-summary' ? (
          <ComplianceSummaryPreview data={data} />
        ) : (
          <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-auto max-h-[60vh]">{JSON.stringify(data, null, 2)}</pre>
        )}
        <div className="mt-4 flex justify-end">
          <button onClick={() => generateReport(def.endpoint, { format: 'pdf' })}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Download PDF</button>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="bg-gray-50 dark:bg-gray-900/40 rounded-lg px-3 py-2 border border-gray-200 dark:border-gray-700">
      <div className="text-xs text-gray-500 dark:text-gray-400">{label}</div>
      <div className="text-lg font-bold text-gray-900 dark:text-gray-100">{value}</div>
    </div>
  )
}

interface CompGroupRow {
  site?: string; role?: string; platform?: string
  device_count: number; avg_score: number | null; grade?: string | null
  passing?: number; failing?: number
}

function gradeColor(grade?: string | null): string {
  switch ((grade || '').toUpperCase()) {
    case 'A': return 'text-green-600 dark:text-green-400'
    case 'B': return 'text-green-600 dark:text-green-400'
    case 'C': return 'text-amber-600 dark:text-amber-400'
    case 'D': return 'text-orange-600 dark:text-orange-400'
    case 'F': return 'text-red-600 dark:text-red-400'
    default: return 'text-gray-500'
  }
}

/** In-browser HTML preview of the Compliance Summary report (was raw JSON). */
function ComplianceSummaryPreview({ data }: { data: Record<string, unknown> }) {
  const summary = (data.summary as { total_devices?: number; avg_score?: number | null; passing?: number; warning?: number; failing?: number; not_checked?: number }) || {}
  const findings = (data.findings_summary as { critical?: unknown[]; warning?: unknown[] }) || {}
  const startup = (data.startup_mismatch as unknown[]) || []

  const groups: { title: string; key: 'by_site' | 'by_role' | 'by_platform'; col: 'site' | 'role' | 'platform' }[] = [
    { title: 'By Site', key: 'by_site', col: 'site' },
    { title: 'By Role', key: 'by_role', col: 'role' },
    { title: 'By Platform', key: 'by_platform', col: 'platform' },
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 sm:grid-cols-6 gap-3 text-sm">
        <Stat label="Devices" value={summary.total_devices ?? 0} />
        <Stat label="Avg score" value={summary.avg_score ?? '—'} />
        <Stat label="Passing" value={summary.passing ?? 0} />
        <Stat label="Warning" value={summary.warning ?? 0} />
        <Stat label="Failing" value={summary.failing ?? 0} />
        <Stat label="Not checked" value={summary.not_checked ?? 0} />
      </div>

      {groups.map(({ title, key, col }) => {
        const rows = (data[key] as CompGroupRow[]) || []
        if (rows.length === 0) return null
        return (
          <div key={key}>
            <h4 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-2">{title}</h4>
            <div className="overflow-hidden rounded-lg border border-gray-200 dark:border-gray-700">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 dark:bg-gray-900/40 text-gray-500 dark:text-gray-400">
                  <tr><th className="text-left px-3 py-1.5">{col}</th><th className="px-3 py-1.5">Devices</th><th className="px-3 py-1.5">Score</th><th className="px-3 py-1.5">Grade</th><th className="px-3 py-1.5">Pass/Fail</th></tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={i} className="border-t border-gray-100 dark:border-gray-700/60">
                      <td className="px-3 py-1.5 text-gray-800 dark:text-gray-200">{r[col] ?? '—'}</td>
                      <td className="px-3 py-1.5 text-center">{r.device_count}</td>
                      <td className="px-3 py-1.5 text-center">{r.avg_score ?? '—'}</td>
                      <td className={clsx('px-3 py-1.5 text-center font-semibold', gradeColor(r.grade))}>{r.grade ?? '—'}</td>
                      <td className="px-3 py-1.5 text-center text-gray-500">{r.passing ?? '—'}/{r.failing ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      })}

      <div className="flex gap-3 text-sm">
        <Stat label="Critical findings" value={(findings.critical || []).length} />
        <Stat label="Warning findings" value={(findings.warning || []).length} />
        <Stat label="Unsaved configs" value={startup.length} />
      </div>
    </div>
  )
}

function RecentReports({ rows, loading }: { rows: GeneratedReportRow[]; loading: boolean }) {
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [busy, setBusy] = useState(false)
  const [downloading, setDownloading] = useState<number | null>(null)

  const handleDownload = async (id: number, filename: string) => {
    setDownloading(id)
    try {
      await downloadReport(id, filename)
    } catch (err) {
      console.error('Download failed:', err)
      alert('Download failed. The file may no longer exist on the server.')
    } finally {
      setDownloading(null)
    }
  }

  const refresh = () => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['reports'] }) }
  const allChecked = rows.length > 0 && selected.size === rows.length
  const toggle = (id: number) =>
    setSelected((p) => { const n = new Set(p); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleAll = () =>
    setSelected(allChecked ? new Set() : new Set(rows.map((r) => r.id)))

  const removeOne = async (id: number) => {
    if (!confirm('Delete this report?')) return
    setBusy(true)
    try { await deleteReport(id); refresh() } finally { setBusy(false) }
  }
  const removeSelected = async () => {
    if (selected.size === 0 || !confirm(`Delete ${selected.size} report(s)?`)) return
    setBusy(true)
    try { await bulkDeleteReports([...selected]); refresh() } finally { setBusy(false) }
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
        <span className="font-semibold text-gray-800 dark:text-gray-100">Recent Reports</span>
        {selected.size > 0 && (
          <button onClick={removeSelected} disabled={busy}
            className="px-3 py-1.5 text-sm rounded-lg bg-red-600 hover:bg-red-700 disabled:opacity-50 text-white font-medium">
            Delete {selected.size} selected
          </button>
        )}
      </div>
      {loading ? (
        <div className="py-8 text-center text-sm text-gray-400">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="py-8 text-center text-sm text-gray-400">No reports generated yet.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
              <th className="px-5 py-2 w-8">
                <input type="checkbox" checked={allChecked} onChange={toggleAll} aria-label="Select all" />
              </th>
              <th className="px-5 py-2 font-medium">Report</th>
              <th className="px-5 py-2 font-medium">Generated</th>
              <th className="px-5 py-2 font-medium">By</th>
              <th className="px-5 py-2 font-medium">Format</th>
              <th className="px-5 py-2 font-medium">Size</th>
              <th className="px-5 py-2 font-medium"></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                <td className="px-5 py-2">
                  <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)}
                    aria-label={`Select ${r.title}`} />
                </td>
                <td className="px-5 py-2 text-gray-800 dark:text-gray-200">{r.title}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{new Date(r.generated_at).toLocaleString()}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{r.generated_by_username || r.source}</td>
                <td className="px-5 py-2 uppercase text-xs text-gray-500 dark:text-gray-400">{r.format}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{fmtBytes(r.file_size)}</td>
                <td className="px-5 py-2 whitespace-nowrap">
                  <button onClick={() => handleDownload(r.id, `${r.title}.${r.format}`)}
                    disabled={downloading === r.id}
                    className="text-blue-600 hover:text-blue-800 dark:text-blue-400 disabled:opacity-50">
                    {downloading === r.id ? 'Downloading…' : 'Download'}</button>
                  <button onClick={() => removeOne(r.id)} disabled={busy}
                    className="ml-3 text-red-600 hover:text-red-800 dark:text-red-400 disabled:opacity-50">Delete</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

const inputCls = 'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-xl border border-gray-200 dark:border-gray-700 w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-900 dark:text-gray-100">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        {children}
      </div>
    </div>
  )
}

const PERIODS = [
  { value: 'daily', label: 'Daily' },
  { value: 'weekly', label: 'Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'quarterly', label: 'Quarterly' },
] as const

function GenerateModal({ def, onClose, onDone }: { def: ReportDef; onClose: () => void; onDone: () => void }) {
  const [format, setFormat] = useState(def.formats[0])
  const [groupBy, setGroupBy] = useState<string[]>(['site', 'role', 'platform'])
  const [date, setDate] = useState<'yesterday' | 'today' | string>('yesterday')
  const [period, setPeriod] = useState<'daily' | 'weekly' | 'monthly' | 'quarterly'>('daily')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const toggle = (g: string) =>
    setGroupBy((p) => (p.includes(g) ? p.filter((x) => x !== g) : [...p, g]))

  const submit = async () => {
    setBusy(true); setErr(null)
    try {
      const body: Record<string, unknown> = { format }
      if (def.hasGroupBy) body.group_by = groupBy
      if (def.hasDate) {
        const endDate = date === 'today' ? new Date().toISOString().slice(0, 10)
          : date === 'yesterday' ? null : date
        if (period === 'daily') {
          body.date = endDate
          await generateReport(def.endpoint, body)
        } else {
          // Weekly/monthly/quarterly go through the period-aware ops endpoint.
          body.period = period
          body.end_date = endDate
          await generateReport('ops', body)
        }
      } else {
        await generateReport(def.endpoint, body)
      }
      onDone()
    } catch {
      setErr('Generation failed.'); setBusy(false)
    }
  }

  return (
    <Modal title={`Generate — ${def.title}`} onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Format</label>
          <div className="flex gap-3">
            {def.formats.map((f) => (
              <label key={f} className="flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-300">
                <input type="radio" checked={format === f} onChange={() => setFormat(f)} /> {f.toUpperCase()}
              </label>
            ))}
          </div>
        </div>
        {def.hasGroupBy && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Group by</label>
            <div className="flex gap-3">
              {['site', 'role', 'platform'].map((g) => (
                <label key={g} className="flex items-center gap-1.5 text-sm capitalize text-gray-700 dark:text-gray-300">
                  <input type="checkbox" checked={groupBy.includes(g)} onChange={() => toggle(g)} /> {g}
                </label>
              ))}
            </div>
          </div>
        )}
        {def.hasDate && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Period</label>
            <select className={inputCls} value={period}
              onChange={(e) => setPeriod(e.target.value as typeof period)}>
              {PERIODS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
            </select>
          </div>
        )}
        {def.hasDate && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">
              {period === 'daily' ? 'Date' : 'Period ending'}
            </label>
            <select className={inputCls} value={['yesterday', 'today'].includes(date) ? date : 'pick'}
              onChange={(e) => setDate(e.target.value === 'pick' ? new Date().toISOString().slice(0, 10) : e.target.value)}>
              <option value="yesterday">Yesterday</option>
              <option value="today">Today</option>
              <option value="pick">Pick a date…</option>
            </select>
            {!['yesterday', 'today'].includes(date) && (
              <input type="date" className={`${inputCls} mt-2`} value={date} onChange={(e) => setDate(e.target.value)} />
            )}
          </div>
        )}
        {err && <p className="text-xs text-red-600">{err}</p>}
        <button onClick={submit} disabled={busy}
          className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
          {busy ? 'Generating…' : 'Generate & Download'}
        </button>
        {format !== 'json' && <p className="text-xs text-gray-400">The file downloads to your browser.</p>}
      </div>
    </Modal>
  )
}

function ScheduleModal({ def, onClose, onDone }: { def: ReportDef; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient()
  // Hour is entered/shown in the user's own timezone; the backend stores it as
  // UTC and converts at the boundary (see apps.reports.schedule_tz).
  const userTz = usePreferencesStore((s) => s.prefs?.timezone) || 'UTC'
  const schedQ = useQuery({ queryKey: ['report-schedules', def.endpoint], queryFn: () => fetchReportSchedules(def.endpoint) })
  const [frequency, setFrequency] = useState<'daily' | 'weekly' | 'monthly' | 'quarterly'>('daily')
  const [hour, setHour] = useState(8)
  const [dayOfWeek, setDayOfWeek] = useState(0)
  const [dayOfMonth, setDayOfMonth] = useState(1)
  const [fmt, setFmt] = useState(def.formats[0])
  const [delivery, setDelivery] = useState<'email' | 'store_only' | 'both'>('email')
  const [recipients, setRecipients] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const emailNeeded = delivery === 'email' || delivery === 'both'

  const refresh = () => qc.invalidateQueries({ queryKey: ['report-schedules', def.endpoint] })

  const save = async () => {
    const list = recipients.split(',').map((s) => s.trim()).filter(Boolean)
    if (emailNeeded && list.length === 0) { setErr('Add at least one recipient email.'); return }
    setBusy(true); setErr(null)
    try {
      // Send only the cadence field the chosen frequency uses (weekly → day_of_week,
      // monthly/quarterly → day_of_month); daily needs neither.
      const body: Record<string, unknown> = {
        frequency, hour, fmt, delivery, recipients: emailNeeded ? list : [],
      }
      if (frequency === 'weekly') body.day_of_week = dayOfWeek
      if (frequency === 'monthly' || frequency === 'quarterly') body.day_of_month = dayOfMonth
      await createReportSchedule(def.endpoint, body)
      refresh(); onDone()
    } catch {
      setErr('Failed to save schedule.'); setBusy(false)
    }
  }

  const remove = async (id: number) => { await deleteReportSchedule(id); refresh() }

  return (
    <Modal title={`Schedule — ${def.title}`} onClose={onClose}>
      <div className="space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Frequency</label>
            <select className={inputCls} value={frequency}
              onChange={(e) => setFrequency(e.target.value as 'daily' | 'weekly' | 'monthly' | 'quarterly')}>
              <option value="daily">Daily</option>
              <option value="weekly">Weekly</option>
              <option value="monthly">Monthly</option>
              <option value="quarterly">Quarterly</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Hour ({userTz})</label>
            <select className={inputCls} value={hour} onChange={(e) => setHour(Number(e.target.value))}>
              {Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}
            </select>
          </div>
        </div>
        {frequency === 'weekly' && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Day of week</label>
            <select className={inputCls} value={dayOfWeek} onChange={(e) => setDayOfWeek(Number(e.target.value))}>
              {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'].map((d, i) => <option key={d} value={i}>{d}</option>)}
            </select>
          </div>
        )}
        {(frequency === 'monthly' || frequency === 'quarterly') && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Day of month</label>
            <select className={inputCls} value={dayOfMonth} onChange={(e) => setDayOfMonth(Number(e.target.value))}>
              {Array.from({ length: 28 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1}</option>)}
            </select>
            <p className="text-xs text-gray-400 mt-1">
              {frequency === 'quarterly'
                ? 'Fires in Jan, Apr, Jul & Oct on this day. Capped at 28 so no month is skipped.'
                : 'Capped at 28 so no month is skipped.'}
            </p>
          </div>
        )}
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Format</label>
          <select className={inputCls} value={fmt} onChange={(e) => setFmt(e.target.value)}>
            {def.formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Delivery</label>
          <select className={inputCls} value={delivery}
            onChange={(e) => setDelivery(e.target.value as 'email' | 'store_only' | 'both')}>
            <option value="email">Email</option>
            <option value="store_only">Store only (download from history)</option>
            <option value="both">Email + Store</option>
          </select>
          {delivery === 'store_only' && (
            <p className="text-xs text-gray-400 mt-1">Generated and saved to Recent Reports — no email sent.</p>
          )}
        </div>
        {emailNeeded && (
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Email recipients (comma-separated)</label>
            <input className={inputCls} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="admin@company.com, noc@company.com" />
          </div>
        )}
        {err && <p className="text-xs text-red-600">{err}</p>}
        <button onClick={save} disabled={busy} className="w-full px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
          {busy ? 'Saving…' : 'Save Schedule'}
        </button>

        {(schedQ.data ?? []).length > 0 && (
          <div className="pt-2 border-t border-gray-100 dark:border-gray-700">
            <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">Existing schedules</p>
            <ul className="space-y-1">
              {schedQ.data!.map((s: ReportScheduleRow) => (
                <li key={s.id} className="flex items-center justify-between text-sm">
                  <span className="text-gray-700 dark:text-gray-300">
                    {formatCadence(s, userTz)} · {s.fmt.toUpperCase()}
                    {' → '}
                    {s.delivery === 'store_only'
                      ? 'Store only'
                      : `${s.delivery === 'both' ? 'Store + email ' : 'Email '}${s.recipients.join(', ')}`}
                  </span>
                  <button onClick={() => remove(s.id)} className="text-red-600 hover:text-red-800 text-xs">Delete</button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Modal>
  )
}
