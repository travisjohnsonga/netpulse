import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchDeviceRiskScore, fetchDeviceAlerts, fetchMonitoredInterfaces, fetchRecentConfigs, fetchCredential,
  fetchDeviceMetrics, enrichDevice, checkHostname, fetchDeviceAudit,
  type DeviceDetail, type RiskScore, type AlertEvent, type RecentConfig, type DeviceMetrics,
  type AuditLogEntry, UNIFI_CONSOLE_PLATFORMS,
} from '../../api/client'
import Gauge from '../../components/Gauge'
import DeviceEditModal from '../../components/DeviceEditModal'
import ConsoleTelemetry from './ConsoleTelemetry'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  high: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  medium: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  low: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  info: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400',
}

export default function Overview({ device, onTab, onRefresh, onManageCredentials }: {
  device: DeviceDetail
  onTab: (t: string) => void
  onRefresh?: () => void
  onManageCredentials?: () => void
}) {
  const [risk, setRisk] = useState<RiskScore | null>(null)
  const [alerts, setAlerts] = useState<AlertEvent[]>([])
  const [alertsLoaded, setAlertsLoaded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [ifaceCount, setIfaceCount] = useState<number | null>(null)
  const [configs, setConfigs] = useState<RecentConfig[] | null>(null)
  const [credName, setCredName] = useState<string | null>(null)
  const [metrics, setMetrics] = useState<DeviceMetrics | null>(null)
  const [audit, setAudit] = useState<AuditLogEntry[] | null>(null)

  useEffect(() => {
    fetchDeviceAudit(device.id, 10).then(setAudit).catch(() => setAudit([]))
    fetchDeviceRiskScore(device.id).then(setRisk).catch(() => setRisk(null))
    fetchDeviceAlerts(device.id, device.hostname)
      .then((a) => setAlerts(a.slice(0, 5)))
      .catch(() => setAlerts([]))
      .finally(() => setAlertsLoaded(true))
    fetchMonitoredInterfaces(device.id).then((m) => setIfaceCount(m.length)).catch(() => setIfaceCount(null))
    fetchDeviceMetrics(device.id).then(setMetrics).catch(() => setMetrics(null))
    fetchRecentConfigs(device.id, 3).then(setConfigs).catch(() => setConfigs([]))
    if (device.credential_profile) {
      fetchCredential(device.credential_profile).then((p) => setCredName(p.name)).catch(() => setCredName(null))
    } else {
      setCredName(null)
    }
  }, [device.id, device.hostname, device.credential_profile])

  const relTime = (iso: string) => {
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.round(s / 60)}m ago`
    if (s < 86400) return `${Math.round(s / 3600)}h ago`
    return `${Math.round(s / 86400)}d ago`
  }

  // Enrichment status: a freshly-approved device's details fill in shortly
  // after a background SNMP/SSH probe. Show a spinner + auto-refresh while
  // that's expected, and a re-run option once it's clearly stalled.
  const [enriching, setEnriching] = useState(false)
  const needsDetails = !device.model && !device.os_version
  const ageSec = Math.max(0, (Date.now() - new Date(device.created_at).getTime()) / 1000)
  const justAdded = ageSec < 90

  useEffect(() => {
    if (!needsDetails) { setEnriching(false); return }
    // Auto-refresh once after 5s for a just-added device (gives enrichment time).
    if (justAdded || enriching) {
      const t = setTimeout(() => onRefresh?.(), 5000)
      return () => clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device.id, needsDetails, justAdded, enriching])

  const rerunEnrich = async () => {
    setEnriching(true)
    try { await enrichDevice(device.id) } catch { /* surfaced by no-change */ }
    setTimeout(() => onRefresh?.(), 5000)
  }

  const [verifyingHostname, setVerifyingHostname] = useState(false)
  const [verifyMsg, setVerifyMsg] = useState<string | null>(null)
  const verifyHostname = async () => {
    setVerifyingHostname(true); setVerifyMsg(null)
    try {
      const r = await checkHostname(device.id)
      setVerifyMsg(r.hostname_changed ? `Updated: ${r.old_hostname} → ${r.new_hostname}` : 'Hostname is current')
      if (r.hostname_changed) onRefresh?.()
    } catch {
      setVerifyMsg('Verification failed')
    } finally {
      setVerifyingHostname(false)
      setTimeout(() => setVerifyMsg(null), 4000)
    }
  }

  const reachable = device.status === 'active'

  const m = metrics?.metrics
  const fmtUptime = (sec: number | null | undefined) => {
    if (sec == null) return '—'
    const s = Math.floor(sec), d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), mm = Math.floor((s % 3600) / 60)
    return d > 0 ? `${d}d ${h}h` : h > 0 ? `${h}h ${mm}m` : `${mm}m`
  }

  const isUnifiConsole = UNIFI_CONSOLE_PLATFORMS.includes(device.platform)

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* UniFi console (UDM / Cloud Key) controller + WAN telemetry */}
      {isUnifiConsole && <ConsoleTelemetry device={device} />}

      {/* Device info */}
      <Card className="lg:col-span-2">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Device Information</h3>
          <button onClick={() => setEditing(true)} className="px-3 py-1 text-xs border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 font-medium">Edit Device</button>
        </div>
        {needsDetails && (enriching || justAdded) && (
          <div className="mb-3 flex items-center gap-2 rounded-lg bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 px-3 py-2 text-xs text-blue-700 dark:text-blue-300">
            <span className="w-3 h-3 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
            Enriching device info… (model, OS version and serial fill in automatically)
          </div>
        )}
        {needsDetails && !enriching && !justAdded && (
          <div className="mb-3 rounded-lg bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 px-3 py-2 text-xs text-amber-800 dark:text-amber-300 flex items-center gap-3 flex-wrap">
            <span>⚠️ Could not auto-detect device details.</span>
            <button onClick={() => setEditing(true)} className="underline hover:no-underline">Edit Device</button>
            <span className="text-amber-400">·</span>
            <button onClick={rerunEnrich} className="underline hover:no-underline">Re-run Enrichment</button>
          </div>
        )}
        <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3 text-sm">
          <Info label="Hostname" value={device.hostname} />
          <Info label="IP Address" value={device.ip_address} mono />
          <div>
            <dt className="text-xs text-gray-400 dark:text-gray-500">Management IP</dt>
            <dd className="flex items-center gap-1.5 font-mono text-xs text-gray-800 dark:text-gray-100">
              {device.management_ip || '—'}
              {device.ip_locked && (
                <span title="Locked — UniFi sync won't overwrite this IP" className="text-amber-500 dark:text-amber-400">
                  <svg className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
                    <path fillRule="evenodd" d="M5 9V7a5 5 0 0 1 10 0v2a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2Zm8-2v2H7V7a3 3 0 0 1 6 0Z" clipRule="evenodd" />
                  </svg>
                </span>
              )}
            </dd>
          </div>
          <Info label="Vendor" value={device.vendor || '—'} />
          <Info label="Model" value={device.model || '—'} />
          <Info label="Platform" value={device.platform || '—'} />
          <Info label="OS Version" value={device.os_version || '—'} />
          <Info label="Serial" value={device.serial_number || '—'} mono />
          <Info label="Added" value={new Date(device.created_at).toLocaleDateString()} />
          <div>
            <dt className="text-xs text-gray-400 dark:text-gray-500">Collector</dt>
            <dd className="text-gray-800 dark:text-gray-100 flex items-center gap-1.5">
              {device.collector_name ? (
                <>
                  <span className={clsx('w-1.5 h-1.5 rounded-full', device.collector_status === 'active' ? 'bg-green-500' : 'bg-gray-400')} />
                  {device.collector_name}
                  {device.collector_ip ? <span className="font-mono text-xs text-gray-500 dark:text-gray-400">({device.collector_ip})</span> : null}
                </>
              ) : '—'}
            </dd>
          </div>
        </dl>
        <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700 flex items-center gap-3 flex-wrap text-xs text-gray-500 dark:text-gray-400">
          <span>Hostname last verified: <span className="text-gray-700 dark:text-gray-300">{hostnameVerifiedLabel(device.hostname_verified_at)}</span></span>
          <button onClick={verifyHostname} disabled={verifyingHostname}
            className="px-2 py-0.5 border border-gray-300 dark:border-gray-600 rounded-md hover:bg-gray-50 dark:hover:bg-gray-700/50 disabled:opacity-50 font-medium">
            {verifyingHostname ? 'Verifying…' : 'Verify Now'}
          </button>
          {verifyMsg && <span className="text-blue-600 dark:text-blue-400">{verifyMsg}</span>}
        </div>
      </Card>

      {/* Status indicators */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Status</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Reachability</span>
            <span className={clsx('inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full',
              reachable ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' : 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400')}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', reachable ? 'bg-green-500' : 'bg-red-500')} />
              {reachable ? 'Reachable' : 'Unreachable'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Last updated</span>
            <span className="text-xs text-gray-500 dark:text-gray-400">{new Date(device.updated_at).toLocaleString()}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600 dark:text-gray-400">Credentials</span>
            <span className="flex items-center gap-2 text-sm">
              {device.credential_profile
                ? <span className="text-gray-800 dark:text-gray-100">{credName ?? 'profile'} ✅</span>
                : <span className="text-gray-400 dark:text-gray-500">None</span>}
              <button onClick={() => onManageCredentials?.()} className="text-xs font-medium text-blue-600 hover:text-blue-800">Manage</button>
            </span>
          </div>
        </div>
      </Card>

      {/* Risk gauge */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Risk Score</h3>
        {risk ? (
          <>
            <Gauge value={Number(risk.score)} label="risk" invert />
            <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 dark:text-gray-400 mt-1">
              <SubScore label="CVE" value={risk.cve_score} />
              <SubScore label="Compliance" value={risk.compliance_score} />
              <SubScore label="Lifecycle" value={risk.lifecycle_score} />
              <SubScore label="Anomaly" value={risk.anomaly_score} />
            </div>
          </>
        ) : (
          <p className="text-sm text-gray-400 dark:text-gray-500 py-8 text-center">No risk score computed yet.</p>
        )}
      </Card>

      {/* Quick stats */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Quick Stats</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
          <Stat label="Uptime" value={fmtUptime(m?.uptime_seconds)} />
          <Stat label="CPU" value={m?.cpu_pct != null ? `${m.cpu_pct.toFixed(0)}%` : '—'} />
          <Stat label="Memory" value={m?.memory_used_pct != null ? `${m.memory_used_pct.toFixed(0)}%` : '—'} />
          <Stat label="Ping" value={metrics?.reachability?.rtt_ms != null ? `${metrics.reachability.rtt_ms.toFixed(1)}ms` : '—'} />
        </div>
        <button onClick={() => onTab('telemetry')} className="block w-full text-center text-sm font-medium text-gray-700 dark:text-gray-300 mt-3 hover:text-blue-700">
          {ifaceCount ?? 0} interface{ifaceCount === 1 ? '' : 's'} monitored →
        </button>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-2">Live metrics appear once the telemetry pipeline reports for this device.</p>
      </Card>

      {/* Recent alerts */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Recent Alerts</h3>
        </div>
        {!alertsLoaded ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">Loading…</p>
        ) : alerts.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">No recent alerts for this device.</p>
        ) : (
          <ul className="space-y-2">
            {alerts.map((a) => (
              <li key={a.id} className="flex items-center gap-2 text-sm">
                <span className={clsx('px-1.5 py-0.5 rounded text-xs font-medium capitalize', SEVERITY_BADGE[a.severity] ?? SEVERITY_BADGE.info)}>{a.severity}</span>
                <span className="truncate text-gray-700 dark:text-gray-300">{a.rule_name}</span>
                <span className="ml-auto text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">{new Date(a.created_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Recent config changes (live) */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Recent Config Changes</h3>
        {configs === null ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">Loading…</p>
        ) : configs.length === 0 ? (
          <div className="text-sm text-gray-400 dark:text-gray-500">
            No configurations collected yet.
            <button onClick={() => onTab('configuration')} className="block mt-2 text-xs font-medium text-blue-600 hover:text-blue-800">Collect Now →</button>
          </div>
        ) : (
          <>
            <ul className="space-y-2 text-sm">
              {configs.map((c) => (
                <li key={c.id} className="flex items-center gap-2">
                  <span className="text-gray-700 dark:text-gray-300">Running config</span>
                  {c.changed_from_previous && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">changed</span>}
                  <span className="ml-auto text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">{c.collected_by} · {relTime(c.collected_at)}</span>
                </li>
              ))}
            </ul>
            <button onClick={() => onTab('configuration')} className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800">View configuration →</button>
          </>
        )}
      </Card>

      {/* Audit history (config pushes/backups, edits, discovery) */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100 mb-3">Audit History</h3>
        {audit === null ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">Loading…</p>
        ) : audit.length === 0 ? (
          <p className="text-sm text-gray-400 dark:text-gray-500">No audit events for this device yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {audit.map((a) => (
              <li key={a.id} className="flex items-center gap-2">
                <span aria-hidden>{a.success ? '✅' : '❌'}</span>
                <span className="text-gray-700 dark:text-gray-300">{a.event_label}</span>
                {a.username && <span className="text-xs text-gray-400">by {a.username}</span>}
                <span className="ml-auto text-xs text-gray-400 dark:text-gray-500 whitespace-nowrap">{relTime(a.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {editing && (
        <DeviceEditModal
          device={device}
          onClose={() => setEditing(false)}
          onSaved={() => { setEditing(false); onRefresh?.() }}
        />
      )}
    </div>
  )
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={clsx('bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 p-4', className)}>{children}</div>
}

// "2h ago" / "3d ago" / "just now" — or "never" when the hostname has not yet
// been verified against the network.
function hostnameVerifiedLabel(iso?: string | null): string {
  if (!iso) return 'never'
  const sec = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000)
  if (sec < 60) return 'just now'
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return `${Math.floor(sec / 86400)}d ago`
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt>
      <dd className={clsx('text-gray-800 dark:text-gray-100', mono && 'font-mono text-xs')}>{value}</dd>
    </div>
  )
}

function SubScore({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between"><span>{label}</span><span className="font-medium text-gray-700 dark:text-gray-300">{Number(value).toFixed(0)}</span></div>
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-lg font-bold text-gray-900 dark:text-gray-100">{value}</p>
      <p className="text-xs text-gray-400 dark:text-gray-500">{label}</p>
    </div>
  )
}
