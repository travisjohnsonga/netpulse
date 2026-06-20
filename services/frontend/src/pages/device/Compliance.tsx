import { useCallback, useEffect, useState, type ReactNode } from 'react'
import clsx from 'clsx'
import {
  fetchComplianceResults, fetchDeviceCompliance, runComplianceDevice,
  fetchApprovedOSVersions,
  type DeviceDetail, type ComplianceResult,
  type ComplianceTemplateResult, type ComplianceFinding,
  type ApprovedOSVersion, type OSInventoryStatus,
  type DeviceComplianceResponse, type ComplianceBreakdownItem,
  type InterfaceRuleFinding, type RoleConsistencyFinding, type StartupStatus,
} from '../../api/client'
import EmptyState from '../../components/EmptyState'
import OSStatusBadge from '../../components/OSStatusBadge'

const GRADE_COLOR: Record<string, string> = {
  A: 'text-green-600 dark:text-green-400',
  B: 'text-green-600 dark:text-green-400',
  C: 'text-yellow-600 dark:text-yellow-400',
  D: 'text-orange-600 dark:text-orange-400',
  F: 'text-red-600 dark:text-red-400',
  'N/A': 'text-gray-400',
}

function barColor(score: number): string {
  if (score >= 90) return 'bg-green-500'
  if (score >= 80) return 'bg-green-400'
  if (score >= 70) return 'bg-yellow-500'
  if (score >= 60) return 'bg-orange-500'
  return 'bg-red-500'
}

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
  const [data, setData] = useState<DeviceComplianceResponse | null>(null)
  const [legacy, setLegacy] = useState<ComplianceResult[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    Promise.all([fetchDeviceCompliance(device.id), fetchComplianceResults(device.id)])
      .then(([tpl, leg]) => {
        setData(tpl); setLegacy(leg); setError(null)
      })
      .catch(() => setError('Failed to load compliance results.'))
      .finally(() => setLoading(false))
  }, [device.id])

  useEffect(load, [load])

  const runCheck = async () => {
    setRunning(true)
    try {
      await runComplianceDevice(device.id)   // re-runs + persists the weighted score
      load()
    } catch {
      setError('Failed to run compliance check.')
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <Spinner />
  if (error) return <Banner text={error} />

  const templateResults = data?.results ?? []
  const interfaceFindings = data?.interface_rule_findings ?? []
  const roleFindings = data?.role_consistency_findings ?? []
  const breakdown = data?.breakdown ?? []
  const hasTemplates = templateResults.length > 0
  const hasInterface = interfaceFindings.length > 0
  const hasRole = roleFindings.length > 0
  const hasLegacy = legacy.length > 0
  const hasAny = hasTemplates || hasInterface || hasRole || hasLegacy

  if (!hasAny) {
    return (
      <div className="space-y-4">
        <div className="flex justify-end">
          <RunButton running={running} onClick={runCheck} />
        </div>
        <OSVersionCard device={device} />
        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
          <EmptyState title="No compliance checks"
            description="This device hasn't been evaluated against any compliance template, interface rule, or role-consistency rule yet. Define rules under Settings → Compliance, then run a check."
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

      <OSVersionCard device={device} />

      {breakdown.length > 0 && (
        <ScoreHeader score={data?.score ?? null} grade={data?.grade ?? 'N/A'} breakdown={breakdown} />
      )}

      {data?.startup_status && <StartupCard status={data.startup_status} />}

      {hasTemplates && (
        <Section title="Template Findings">
          <div className="space-y-3">
            {templateResults.map((r) => <TemplateResultCard key={r.id} result={r} />)}
          </div>
        </Section>
      )}

      {hasInterface && (
        <Section title="Interface Rule Findings">
          <div className="space-y-3">
            {interfaceFindings.map((f, i) => <InterfaceFindingCard key={i} finding={f} platform={device.platform} />)}
          </div>
        </Section>
      )}

      {hasRole && (
        <Section title="Role Consistency">
          <div className="space-y-3">
            {roleFindings.map((f, i) => <RoleConsistencyCard key={i} finding={f} />)}
          </div>
        </Section>
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

function StartupCard({ status }: { status: StartupStatus }) {
  const unsaved = status.added + status.removed
  function relTime(iso: string | null): string {
    if (!iso) return 'unknown'
    const secs = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
    if (secs < 60) return 'just now'
    const mins = Math.round(secs / 60)
    if (mins < 60) return `${mins} minute${mins === 1 ? '' : 's'} ago`
    const hrs = Math.round(mins / 60)
    if (hrs < 24) return `${hrs} hour${hrs === 1 ? '' : 's'} ago`
    return `${Math.round(hrs / 24)} day(s) ago`
  }

  if (status.match) {
    return (
      <div className="bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg px-4 py-3">
        <div className="text-sm font-medium text-green-800 dark:text-green-300">✅ Running/Startup Config Match</div>
        <div className="text-xs text-green-700 dark:text-green-400 mt-0.5">Last checked: {relTime(status.checked_at)}</div>
      </div>
    )
  }

  return (
    <div className="bg-amber-50 dark:bg-amber-900/20 border border-amber-300 dark:border-amber-800 rounded-lg p-4 space-y-3">
      <div>
        <div className="text-sm font-semibold text-amber-800 dark:text-amber-300">⚠️ Running/Startup Config Mismatch</div>
        <p className="text-sm text-amber-700 dark:text-amber-400 mt-0.5">
          Running config has {unsaved} unsaved change{unsaved === 1 ? '' : 's'}. These will be <strong>LOST on next reboot!</strong>
        </p>
      </div>
      {status.diff && (
        <div>
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">Diff (running vs startup)</div>
          <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-700 rounded p-3 overflow-x-auto max-h-64">
            {status.diff.split('\n').map((line, i) => (
              <div key={i} className={clsx(
                line.startsWith('+') && !line.startsWith('+++') && 'text-green-600 dark:text-green-400',
                line.startsWith('-') && !line.startsWith('---') && 'text-red-600 dark:text-red-400',
              )}>{line || ' '}</div>
            ))}
          </pre>
        </div>
      )}
      <CopyBlock label="Connect to the device and run:" text="write memory" />
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-200 mb-2">{title}</h3>
      {children}
    </div>
  )
}

function ScoreHeader({ score, grade, breakdown }: {
  score: number | null; grade: string; breakdown: ComplianceBreakdownItem[]
}) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-5">
      <div className="flex items-center gap-6 mb-4">
        <div className="text-center shrink-0">
          <div className="text-4xl font-bold text-gray-900 dark:text-gray-100">
            {score == null ? '—' : Math.round(score)}<span className="text-xl text-gray-400">/100</span>
          </div>
          <div className={clsx('text-sm font-semibold', GRADE_COLOR[grade] ?? 'text-gray-400')}>Grade: {grade}</div>
        </div>
        <div className="flex-1">
          <div className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-1">Overall Compliance Score</div>
          <div className="h-3 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
            <div className={clsx('h-full rounded-full transition-all', barColor(score ?? 0))} style={{ width: `${score ?? 0}%` }} />
          </div>
        </div>
      </div>
      <div className="space-y-2">
        {breakdown.map((b) => (
          <div key={b.name} className="flex items-center gap-3 text-xs">
            <span className="w-36 shrink-0 text-gray-600 dark:text-gray-300">{b.name}</span>
            <span className="w-16 shrink-0 font-mono text-gray-700 dark:text-gray-200">{Math.round(b.score)}/100</span>
            <div className="flex-1 h-2 rounded-full bg-gray-100 dark:bg-gray-700 overflow-hidden">
              <div className={clsx('h-full rounded-full', barColor(b.score))} style={{ width: `${b.score}%` }} />
            </div>
            <span className="w-10 shrink-0 text-right text-gray-400">({b.weight}%)</span>
            {b.total != null && <span className="w-16 shrink-0 text-right text-gray-400">{b.passing}/{b.total}</span>}
          </div>
        ))}
      </div>
    </div>
  )
}

function CopyBlock({ text, label }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(text).then(() => {
      setCopied(true); setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }
  return (
    <div>
      {label && <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-1">{label}</div>}
      <div className="relative">
        <pre className="text-xs font-mono bg-gray-50 dark:bg-gray-950 border border-gray-200 dark:border-gray-700 rounded p-3 pr-16 overflow-x-auto text-gray-800 dark:text-gray-200 whitespace-pre-wrap">{text}</pre>
        <button onClick={copy}
          className="absolute top-2 right-2 px-2 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-600 dark:text-gray-300">
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
    </div>
  )
}

function InterfaceFindingCard({ finding, platform }: { finding: InterfaceRuleFinding; platform: string }) {
  const [open, setOpen] = useState(!finding.passed)
  const failedChecks = finding.findings.filter((c) => !c.passed)
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center justify-between px-4 py-3 text-left">
        <div className="flex items-center gap-2 min-w-0">
          <span>{finding.passed ? '✅' : '❌'}</span>
          <span className="font-mono text-sm text-gray-900 dark:text-gray-100">{finding.interface}</span>
          {finding.neighbor && <span className="text-xs text-gray-400">→ {finding.neighbor}</span>}
          <span className="text-xs text-gray-500 dark:text-gray-400">· {finding.rule_name}</span>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className={clsx('text-xs font-medium', finding.passed ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
            {finding.passing}/{finding.total} pass
          </span>
          <span className="text-gray-400">{open ? '▴' : '▾'}</span>
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 border-t border-gray-100 dark:border-gray-700 pt-3 space-y-3">
          <ul className="space-y-1.5 text-sm">
            {finding.findings.map((c, i) => (
              <li key={i} className="flex items-center gap-2">
                <span>{c.passed ? '✅' : '❌'}</span>
                <span className={clsx(c.passed ? 'text-gray-600 dark:text-gray-300' : 'text-red-600 dark:text-red-400')}>
                  {c.description || c.value}: {c.passed ? 'PASS' : 'MISSING'}
                </span>
              </li>
            ))}
          </ul>

          {!finding.passed && failedChecks[0]?.value && (
            <p className="text-xs text-gray-500 dark:text-gray-400">
              Expected to find: <span className="font-mono text-gray-700 dark:text-gray-200">"{failedChecks[0].value}"</span>
            </p>
          )}

          {finding.interface_config && (
            <CopyBlock label="Interface config" text={finding.interface_config} />
          )}

          {finding.suggested_fix && (
            <CopyBlock label={`Suggested fix${platform ? ` (${platform})` : ''}`} text={finding.suggested_fix} />
          )}
        </div>
      )}
    </div>
  )
}

function RoleConsistencyCard({ finding }: { finding: RoleConsistencyFinding }) {
  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4 space-y-2">
      <div className="flex items-center gap-2">
        <span>{finding.passed ? '✅' : '❌'}</span>
        <span className="font-medium text-gray-900 dark:text-gray-100">{finding.rule_name}</span>
      </div>
      <div className="text-sm space-y-1">
        {finding.missing.length > 0
          ? <div className="text-red-600 dark:text-red-400">❌ Missing: <span className="font-mono">{finding.missing.join(', ')}</span></div>
          : <div className="text-green-600 dark:text-green-400">✅ Nothing missing</div>}
        {finding.extra.length > 0
          ? <div className="text-orange-600 dark:text-orange-400">⚠️ Extra: <span className="font-mono">{finding.extra.join(', ')}</span></div>
          : <div className="text-green-600 dark:text-green-400">✅ No extra items</div>}
      </div>
      <div className="text-xs text-gray-500 dark:text-gray-400 space-y-0.5 font-mono">
        <div>Expected: {finding.expected.join(', ') || '—'}</div>
        <div>This device: {finding.has.join(', ') || '—'}</div>
      </div>
      {finding.remediation && <CopyBlock label="Suggested fix" text={finding.remediation} />}
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
      {running ? 'Running…' : '▶ Run Now'}
    </button>
  )
}

function Spinner() {
  return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
}
function Banner({ text }: { text: string }) {
  return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:bg-yellow-900/20 dark:border-yellow-800 dark:text-yellow-400">{text}</div>
}

// Matches a version string against a policy pattern the same way the backend
// does (Python re.match → anchored at the start only).
function policyMatches(p: ApprovedOSVersion, version: string): boolean {
  if (p.is_regex) {
    try { return new RegExp(`^(?:${p.version_pattern})`).test(version) }
    catch { return false }
  }
  return p.version_pattern === version
}

// Most-urgent status wins, mirroring the backend precedence.
const OS_PRECEDENCE = ['prohibited', 'deprecated', 'preferred', 'approved']

function OSVersionCard({ device }: { device: DeviceDetail }) {
  const [policies, setPolicies] = useState<ApprovedOSVersion[] | null>(null)

  useEffect(() => {
    fetchApprovedOSVersions()
      .then((all) => setPolicies(all.filter((p) => p.platform === device.platform)))
      .catch(() => setPolicies([]))
  }, [device.platform])

  // Don't render until loaded; hide entirely when OS policy isn't in use at all.
  if (policies === null) return null

  const version = device.os_version || ''
  const sorted = [...policies].sort(
    (a, b) => OS_PRECEDENCE.indexOf(a.status) - OS_PRECEDENCE.indexOf(b.status))
  const match = sorted.find((p) => policyMatches(p, version))
  const status: OSInventoryStatus = match ? match.status : 'unknown'

  // Nothing to show if there are no policies for this platform — keeps the tab
  // uncluttered until an admin defines OS policies.
  if (policies.length === 0) return null

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">OS Version Compliance</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm max-w-md">
        <dt className="text-gray-500 dark:text-gray-400">Version</dt>
        <dd className="font-mono text-gray-800 dark:text-gray-200">{version || '—'}</dd>
        <dt className="text-gray-500 dark:text-gray-400">Platform</dt>
        <dd className="font-mono text-gray-800 dark:text-gray-200">{device.platform}</dd>
        <dt className="text-gray-500 dark:text-gray-400">Status</dt>
        <dd><OSStatusBadge status={status} /></dd>
        <dt className="text-gray-500 dark:text-gray-400">Policy</dt>
        <dd className="text-gray-700 dark:text-gray-300">
          {match
            ? <span className="font-mono text-xs">{match.version_pattern}</span>
            : <span className="text-gray-400">No matching policy</span>}
        </dd>
      </dl>
    </div>
  )
}
