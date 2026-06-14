import { useState, type ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  generateReport, fetchReports, downloadReport,
  fetchReportSchedules, createReportSchedule, deleteReportSchedule,
  type GeneratedReportRow, type ReportScheduleRow,
} from '../api/client'

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

function fmtBytes(n: number | null): string {
  if (!n) return '—'
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function Reports() {
  const qc = useQueryClient()
  const [modal, setModal] = useState<{ def: ReportDef; mode: 'generate' | 'schedule' } | null>(null)
  const reportsQ = useQuery({ queryKey: ['reports'], queryFn: fetchReports })

  const close = () => setModal(null)
  const onDone = () => { close(); qc.invalidateQueries({ queryKey: ['reports'] }) }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Reports</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">Generate on-demand reports or schedule recurring email delivery.</p>
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
              <button onClick={() => setModal({ def, mode: 'schedule' })}
                className="px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg text-sm dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700">Schedule</button>
            </div>
          </div>
        ))}
      </div>

      <RecentReports rows={reportsQ.data ?? []} loading={reportsQ.isLoading} />

      {modal && modal.mode === 'generate' && <GenerateModal def={modal.def} onClose={close} onDone={onDone} />}
      {modal && modal.mode === 'schedule' && <ScheduleModal def={modal.def} onClose={close} onDone={onDone} />}
    </div>
  )
}

function RecentReports({ rows, loading }: { rows: GeneratedReportRow[]; loading: boolean }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
      <div className="px-5 py-3 border-b border-gray-200 dark:border-gray-700 font-semibold text-gray-800 dark:text-gray-100">Recent Reports</div>
      {loading ? (
        <div className="py-8 text-center text-sm text-gray-400">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="py-8 text-center text-sm text-gray-400">No reports generated yet.</div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left">
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
                <td className="px-5 py-2 text-gray-800 dark:text-gray-200">{r.title}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{new Date(r.generated_at).toLocaleString()}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{r.generated_by_username || r.source}</td>
                <td className="px-5 py-2 uppercase text-xs text-gray-500 dark:text-gray-400">{r.format}</td>
                <td className="px-5 py-2 text-gray-500 dark:text-gray-400">{fmtBytes(r.file_size)}</td>
                <td className="px-5 py-2">
                  <button onClick={() => downloadReport(r.id, `${r.title}.${r.format}`)}
                    className="text-blue-600 hover:text-blue-800 dark:text-blue-400">Download</button>
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

function GenerateModal({ def, onClose, onDone }: { def: ReportDef; onClose: () => void; onDone: () => void }) {
  const [format, setFormat] = useState(def.formats[0])
  const [groupBy, setGroupBy] = useState<string[]>(['site', 'role', 'platform'])
  const [date, setDate] = useState<'yesterday' | 'today' | string>('yesterday')
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
        body.date = date === 'today' ? new Date().toISOString().slice(0, 10)
          : date === 'yesterday' ? null : date
      }
      await generateReport(def.endpoint, body)
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
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Date</label>
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
  const schedQ = useQuery({ queryKey: ['report-schedules', def.endpoint], queryFn: () => fetchReportSchedules(def.endpoint) })
  const [frequency, setFrequency] = useState<'daily' | 'weekly' | 'monthly'>('daily')
  const [hour, setHour] = useState(8)
  const [dayOfWeek, setDayOfWeek] = useState(0)
  const [fmt, setFmt] = useState(def.formats[0])
  const [recipients, setRecipients] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const refresh = () => qc.invalidateQueries({ queryKey: ['report-schedules', def.endpoint] })

  const save = async () => {
    const list = recipients.split(',').map((s) => s.trim()).filter(Boolean)
    if (list.length === 0) { setErr('Add at least one recipient email.'); return }
    setBusy(true); setErr(null)
    try {
      await createReportSchedule(def.endpoint, {
        frequency, hour, day_of_week: dayOfWeek, fmt, recipients: list,
      })
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
            <select className={inputCls} value={frequency} onChange={(e) => setFrequency(e.target.value as 'daily' | 'weekly' | 'monthly')}>
              <option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Hour (UTC)</label>
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
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Format</label>
          <select className={inputCls} value={fmt} onChange={(e) => setFmt(e.target.value)}>
            {def.formats.map((f) => <option key={f} value={f}>{f.toUpperCase()}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Email recipients (comma-separated)</label>
          <input className={inputCls} value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="admin@company.com, noc@company.com" />
        </div>
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
                    {s.frequency} @ {String(s.hour).padStart(2, '0')}:00 · {s.fmt.toUpperCase()} → {s.recipients.join(', ')}
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
