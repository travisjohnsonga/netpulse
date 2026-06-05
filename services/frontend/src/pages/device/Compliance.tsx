import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchComplianceResults, fetchDeviceCompliance, runComplianceCheck,
  type DeviceDetail, type ComplianceResult,
  type ComplianceTemplateResult, type ComplianceFinding,
} from '../../api/client'
import Gauge from '../../components/Gauge'
import EmptyState from '../../components/EmptyState'

const OUTCOME_BADGE: Record<string, string> = {
  pass: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  fail: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  error: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
}

const FINDING_BADGE: Record<string, string> = {
  MISSING: 'bg-red-50 text-red-600 border-red-200 dark:bg-red-900/20 dark:text-red-400 dark:border-red-800',
  EXTRA: 'bg-orange-50 text-orange-600 border-orange-200 dark:bg-orange-900/20 dark:text-orange-400 dark:border-orange-800',
  DRIFT: 'bg-yellow-50 text-yellow-700 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800',
  ERROR: 'bg-yellow-50 text-yellow-700 border-yellow-200 dark:bg-yellow-900/20 dark:text-yellow-400 dark:border-yellow-800',
}

const STATUS_BADGE: Record<string, string> = {
  compliant: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  non_compliant: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  error: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  skipped: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
}
const STATUS_LABEL: Record<string, string> = {
  compliant: 'Compliant', non_compliant: 'Non-Compliant', error: 'Error', skipped: 'Skipped',
}

export default function Compliance({ device }: { device: DeviceDetail }) {
  const [templateResults, setTemplateResults] = useState<ComplianceTemplateResult[]>([])
  const [overall, setOverall] = useState<number | null>(null)
  const [legacy, setLegacy] = useState<ComplianceResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchDeviceCompliance(device.id), fetchComplianceResults(device.id)])
      .then(([tpl, leg]) => {
        setTemplateResults(tpl.results); setOverall(tpl.overall_score)
        setLegacy(leg); setError(null)
      })
      .catch(() => setError('Failed to load compliance results.'))
      .finally(() => setLoading(false))
  }, [device.id])

  useEffect(load, [load])

  const runCheck = async () => {
    setRunning(true)
    try {
      await runComplianceCheck({ device_id: device.id })
      load()
    } catch {
      setError('Failed to run compliance check.')
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <Spinner />
  if (error) return <Banner text={error} />

  const hasTemplates = templateResults.length > 0
  const hasLegacy = legacy.length > 0

  if (!hasTemplates && !hasLegacy) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">
          <RunButton running={running} onClick={runCheck} />
        </div>
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <EmptyState title="No compliance checks"
            description="This device hasn't been evaluated against any compliance template yet. Define templates under Settings → Compliance, then run a check."
            icon="📐" />
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Configuration Compliance</h2>
        <RunButton running={running} onClick={runCheck} />
      </div>

      {hasTemplates && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Overall score */}
          <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
            <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Overall Score</h3>
            <Gauge value={overall ?? 0} label="% compliant" />
            <p className="text-xs text-gray-400 dark:text-gray-500 text-center">
              {templateResults.filter((r) => r.status === 'compliant').length}/{templateResults.length} templates compliant
            </p>
          </div>

          {/* Per-template results */}
          <div className="lg:col-span-2 space-y-3">
            {templateResults.map((r) => <TemplateResultCard key={r.id} result={r} />)}
          </div>
        </div>
      )}

      {hasLegacy && (
        <details className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden" open={!hasTemplates}>
          <summary className="px-5 py-3 cursor-pointer text-sm font-semibold text-gray-800 dark:text-gray-100">
            Legacy Policy Rules <span className="font-normal text-gray-400">({legacy.length})</span>
          </summary>
          <div className="overflow-x-auto border-t border-gray-200 dark:border-gray-700">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Rule</th>
                  <th className="px-5 py-3 font-medium">Outcome</th>
                  <th className="px-5 py-3 font-medium">Detail</th>
                  <th className="px-5 py-3 font-medium">Checked</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {legacy.map((r) => (
                  <tr key={r.id} className="hover:bg-gray-50 dark:hover:bg-gray-700/50">
                    <td className="px-5 py-3 text-gray-700 dark:text-gray-300">Rule #{r.rule}</td>
                    <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', OUTCOME_BADGE[r.outcome])}>{r.outcome}</span></td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{r.detail || '—'}</td>
                    <td className="px-5 py-3 text-gray-400 dark:text-gray-500 text-xs">{new Date(r.created_at).toLocaleDateString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </div>
  )
}

function TemplateResultCard({ result }: { result: ComplianceTemplateResult }) {
  const [open, setOpen] = useState(false)
  const findings = result.findings || []
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center justify-between px-4 py-3 text-left">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-gray-900 dark:text-gray-100 truncate">{result.template_name ?? `Template #${result.template}`}</span>
            <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', STATUS_BADGE[result.status])}>{STATUS_LABEL[result.status] ?? result.status}</span>
          </div>
          <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
            {result.score !== null ? `${result.score}% · ` : ''}
            {result.missing_count} missing · {result.drift_count} drift · {result.extra_count} extra
            {result.checked_at && ` · ${new Date(result.checked_at).toLocaleString()}`}
          </div>
        </div>
        <span className="text-gray-400 ml-2 shrink-0">{open ? '▴' : '▾'}</span>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 dark:border-gray-700 pt-3 space-y-3">
          {findings.length === 0 ? (
            <p className="text-sm text-green-600 dark:text-green-400">No drift — config matches the template. 🎉</p>
          ) : (
            <ul className="space-y-1.5">
              {findings.map((f, i) => <FindingRow key={i} finding={f} />)}
            </ul>
          )}
          {result.remediation && (
            <div>
              <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Remediation</div>
              <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-x-auto text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{result.remediation}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FindingRow({ finding }: { finding: ComplianceFinding }) {
  return (
    <li className="flex items-start gap-2 text-sm">
      <span className={clsx('text-xs font-medium px-1.5 py-0.5 rounded border shrink-0', FINDING_BADGE[finding.type] ?? FINDING_BADGE.DRIFT)}>{finding.type}</span>
      <span className="text-gray-700 dark:text-gray-300 font-mono break-all">
        {finding.type === 'DRIFT' && finding.expected && finding.actual ? (
          <>expected <span className="text-green-600 dark:text-green-400">{finding.expected}</span>, found <span className="text-red-600 dark:text-red-400">{finding.actual}</span></>
        ) : (
          finding.line || finding.expected || finding.context || '—'
        )}
      </span>
    </li>
  )
}

function RunButton({ running, onClick }: { running: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} disabled={running}
      className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg text-sm font-medium">
      {running ? 'Running…' : 'Run Check'}
    </button>
  )
}

function Spinner() {
  return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
}
function Banner({ text }: { text: string }) {
  return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-400">{text}</div>
}
