import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import {
  fetchFrameworks, fetchFramework, downloadFrameworkReport,
  type ControlStatus, type ControlAssessment,
} from '../api/client'

const STATUS_STYLE: Record<ControlStatus, { label: string; badge: string; dot: string }> = {
  satisfied: { label: 'Satisfied', badge: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400', dot: 'bg-green-500' },
  partial: { label: 'Partial', badge: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400', dot: 'bg-amber-500' },
  gap: { label: 'Gap', badge: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400', dot: 'bg-red-500' },
  not_applicable: { label: 'N/A', badge: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300', dot: 'bg-gray-400' },
}

function coverageColor(c: number | null): string {
  if (c == null) return 'text-gray-400'
  if (c >= 90) return 'text-green-600 dark:text-green-400'
  if (c >= 60) return 'text-amber-600 dark:text-amber-400'
  return 'text-red-600 dark:text-red-400'
}

export default function Compliance() {
  const [selected, setSelected] = useState<string | null>(null)

  const listQ = useQuery({ queryKey: ['frameworks'], queryFn: fetchFrameworks })

  if (listQ.isLoading) {
    return <div className="flex items-center justify-center py-20"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  }
  if (listQ.isError) {
    return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-400">Failed to load frameworks.</div>
  }

  const frameworks = listQ.data ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Regulatory Compliance</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">
          spane maps the operational evidence it collects onto common framework controls. Generate a PDF
          evidence package for auditors. Control catalogs are representative subsets mapped to available signals.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {frameworks.map((f) => (
          <button key={f.key} onClick={() => setSelected(f.key)}
            className={clsx('text-left bg-white dark:bg-gray-800 rounded-lg shadow-sm border p-4 hover:border-blue-400 transition-colors',
              selected === f.key ? 'border-blue-500' : 'border-gray-200 dark:border-gray-700')}>
            <div className="flex items-start justify-between">
              <div className="min-w-0">
                <div className="font-semibold text-gray-900 dark:text-gray-100 truncate">{f.name}</div>
                <div className="text-xs text-gray-400">{f.version}</div>
              </div>
              <div className={clsx('text-2xl font-bold shrink-0', coverageColor(f.coverage))}>
                {f.coverage == null ? '—' : `${Math.round(f.coverage)}%`}
              </div>
            </div>
            <div className="mt-3 h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
              <div className={clsx('h-full rounded-full', (f.coverage ?? 0) >= 90 ? 'bg-green-500' : (f.coverage ?? 0) >= 60 ? 'bg-amber-500' : 'bg-red-500')}
                style={{ width: `${f.coverage ?? 0}%` }} />
            </div>
            <div className="mt-2 flex gap-3 text-xs text-gray-500 dark:text-gray-400">
              <span className="text-green-600 dark:text-green-400">{f.counts.satisfied} satisfied</span>
              <span className="text-amber-600 dark:text-amber-400">{f.counts.partial} partial</span>
              <span className="text-red-600 dark:text-red-400">{f.counts.gap} gap</span>
            </div>
          </button>
        ))}
      </div>

      {selected && <FrameworkDetail key={selected} frameworkKey={selected} />}
    </div>
  )
}

function FrameworkDetail({ frameworkKey }: { frameworkKey: string }) {
  const [downloading, setDownloading] = useState(false)
  const q = useQuery({ queryKey: ['framework', frameworkKey], queryFn: () => fetchFramework(frameworkKey) })

  if (q.isLoading) return <div className="py-8 text-center text-sm text-gray-400">Loading controls…</div>
  if (q.isError || !q.data) return <div className="text-sm text-red-600">Failed to load framework controls.</div>

  const report = q.data
  const download = async () => {
    setDownloading(true)
    try { await downloadFrameworkReport(frameworkKey, report.framework.name) } finally { setDownloading(false) }
  }

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between px-5 py-4 border-b border-gray-200 dark:border-gray-700">
        <div>
          <h2 className="font-semibold text-gray-900 dark:text-gray-100">{report.framework.name} — Controls</h2>
          <p className="text-xs text-gray-400">{report.total_controls} controls · coverage {report.coverage == null ? '—' : `${report.coverage}%`}</p>
        </div>
        <button onClick={download} disabled={downloading}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
          {downloading ? 'Generating…' : '⬇ Download PDF evidence'}
        </button>
      </div>
      <ul className="divide-y divide-gray-100 dark:divide-gray-700">
        {report.controls.map((c) => <ControlRow key={c.control_id} control={c} />)}
      </ul>
    </div>
  )
}

function ControlRow({ control }: { control: ControlAssessment }) {
  const [open, setOpen] = useState(false)
  const style = STATUS_STYLE[control.status]
  return (
    <li>
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-gray-50 dark:hover:bg-gray-700/50">
        <span className={clsx('w-2 h-2 rounded-full shrink-0', style.dot)} />
        <span className="font-mono text-xs text-gray-500 dark:text-gray-400 w-28 shrink-0">{control.control_id}</span>
        <span className="flex-1 min-w-0">
          <span className="block text-sm text-gray-900 dark:text-gray-100 truncate">{control.title}</span>
          <span className="block text-xs text-gray-400 truncate">{control.summary}</span>
        </span>
        <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium shrink-0', style.badge)}>{style.label}</span>
        <span className="text-gray-400 shrink-0">{open ? '▴' : '▾'}</span>
      </button>
      {open && (
        <div className="px-5 pb-4 pl-[4.25rem] space-y-2">
          {control.category && <div className="text-xs text-gray-400">Category: {control.category}</div>}
          {control.evidence.length > 0 ? (
            <ul className="text-sm text-gray-600 dark:text-gray-300 space-y-1 list-disc list-inside">
              {control.evidence.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          ) : (
            <p className="text-sm text-gray-400">No evidence available for this control.</p>
          )}
        </div>
      )}
    </li>
  )
}
