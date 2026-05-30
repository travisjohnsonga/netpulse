import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchDeviceRiskScore, fetchDeviceAlerts, fetchMonitoredInterfaces, fetchRecentConfigs,
  type DeviceDetail, type RiskScore, type AlertEvent, type RecentConfig,
} from '../../api/client'
import Gauge from '../../components/Gauge'
import DeviceEditModal from '../../components/DeviceEditModal'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-blue-100 text-blue-700',
  info: 'bg-gray-100 text-gray-600',
}

export default function Overview({ device, onTab, onRefresh }: {
  device: DeviceDetail
  onTab: (t: string) => void
  onRefresh?: () => void
}) {
  const [risk, setRisk] = useState<RiskScore | null>(null)
  const [alerts, setAlerts] = useState<AlertEvent[]>([])
  const [alertsLoaded, setAlertsLoaded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [ifaceCount, setIfaceCount] = useState<number | null>(null)
  const [configs, setConfigs] = useState<RecentConfig[] | null>(null)

  useEffect(() => {
    fetchDeviceRiskScore(device.id).then(setRisk).catch(() => setRisk(null))
    fetchDeviceAlerts(device.id, device.hostname)
      .then((a) => setAlerts(a.slice(0, 5)))
      .catch(() => setAlerts([]))
      .finally(() => setAlertsLoaded(true))
    fetchMonitoredInterfaces(device.id).then((m) => setIfaceCount(m.length)).catch(() => setIfaceCount(null))
    fetchRecentConfigs(device.id, 3).then(setConfigs).catch(() => setConfigs([]))
  }, [device.id, device.hostname])

  const relTime = (iso: string) => {
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
    if (s < 60) return 'just now'
    if (s < 3600) return `${Math.round(s / 60)}m ago`
    if (s < 86400) return `${Math.round(s / 3600)}h ago`
    return `${Math.round(s / 86400)}d ago`
  }

  const reachable = device.status === 'active'

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Device info */}
      <Card className="lg:col-span-2">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800">Device Information</h3>
          <button onClick={() => setEditing(true)} className="px-3 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50 font-medium">Edit Device</button>
        </div>
        <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-3 text-sm">
          <Info label="Hostname" value={device.hostname} />
          <Info label="IP Address" value={device.ip_address} mono />
          <Info label="Management IP" value={device.management_ip || '—'} mono />
          <Info label="Vendor" value={device.vendor || '—'} />
          <Info label="Model" value={device.model || '—'} />
          <Info label="Platform" value={device.platform || '—'} />
          <Info label="OS Version" value={device.os_version || '—'} />
          <Info label="Serial" value={device.serial_number || '—'} mono />
          <Info label="Added" value={new Date(device.created_at).toLocaleDateString()} />
        </dl>
      </Card>

      {/* Status indicators */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Status</h3>
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600">Reachability</span>
            <span className={clsx('inline-flex items-center gap-1.5 text-xs font-medium px-2 py-0.5 rounded-full',
              reachable ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700')}>
              <span className={clsx('w-1.5 h-1.5 rounded-full', reachable ? 'bg-green-500' : 'bg-red-500')} />
              {reachable ? 'Reachable' : 'Unreachable'}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600">Last updated</span>
            <span className="text-xs text-gray-500">{new Date(device.updated_at).toLocaleString()}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-sm text-gray-600">Credentials</span>
            <button onClick={() => onTab('credentials')} className="text-xs font-medium text-blue-600 hover:text-blue-800">
              {device.credential_profile ? 'Profile assigned' : 'None'} →
            </button>
          </div>
        </div>
      </Card>

      {/* Risk gauge */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800">Risk Score</h3>
        {risk ? (
          <>
            <Gauge value={Number(risk.score)} label="risk" invert />
            <div className="grid grid-cols-2 gap-2 text-xs text-gray-500 mt-1">
              <SubScore label="CVE" value={risk.cve_score} />
              <SubScore label="Compliance" value={risk.compliance_score} />
              <SubScore label="Lifecycle" value={risk.lifecycle_score} />
              <SubScore label="Anomaly" value={risk.anomaly_score} />
            </div>
          </>
        ) : (
          <p className="text-sm text-gray-400 py-8 text-center">No risk score computed yet.</p>
        )}
      </Card>

      {/* Quick stats */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Quick Stats</h3>
        <div className="grid grid-cols-3 gap-3 text-center">
          <Stat label="Uptime" value="—" />
          <Stat label="CPU" value="—" />
          <Stat label="Memory" value="—" />
        </div>
        <button onClick={() => onTab('telemetry')} className="block w-full text-center text-sm font-medium text-gray-700 mt-3 hover:text-blue-700">
          {ifaceCount ?? 0} interface{ifaceCount === 1 ? '' : 's'} monitored →
        </button>
        <p className="text-xs text-gray-400 mt-2">Live metrics appear once the telemetry pipeline reports for this device.</p>
      </Card>

      {/* Recent alerts */}
      <Card>
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-gray-800">Recent Alerts</h3>
        </div>
        {!alertsLoaded ? (
          <p className="text-sm text-gray-400">Loading…</p>
        ) : alerts.length === 0 ? (
          <p className="text-sm text-gray-400">No recent alerts for this device.</p>
        ) : (
          <ul className="space-y-2">
            {alerts.map((a) => (
              <li key={a.id} className="flex items-center gap-2 text-sm">
                <span className={clsx('px-1.5 py-0.5 rounded text-xs font-medium capitalize', SEVERITY_BADGE[a.severity] ?? SEVERITY_BADGE.info)}>{a.severity}</span>
                <span className="truncate text-gray-700">{a.rule_name}</span>
                <span className="ml-auto text-xs text-gray-400 whitespace-nowrap">{new Date(a.created_at).toLocaleDateString()}</span>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* Recent config changes (live) */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Recent Config Changes</h3>
        {configs === null ? (
          <p className="text-sm text-gray-400">Loading…</p>
        ) : configs.length === 0 ? (
          <div className="text-sm text-gray-400">
            No configurations collected yet.
            <button onClick={() => onTab('configuration')} className="block mt-2 text-xs font-medium text-blue-600 hover:text-blue-800">Collect Now →</button>
          </div>
        ) : (
          <>
            <ul className="space-y-2 text-sm">
              {configs.map((c) => (
                <li key={c.id} className="flex items-center gap-2">
                  <span className="text-gray-700">Running config</span>
                  {c.changed_from_previous && <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber-100 text-amber-700">changed</span>}
                  <span className="ml-auto text-xs text-gray-400 whitespace-nowrap">{c.collected_by} · {relTime(c.collected_at)}</span>
                </li>
              ))}
            </ul>
            <button onClick={() => onTab('configuration')} className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800">View configuration →</button>
          </>
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
  return <div className={clsx('bg-white rounded-lg shadow-sm border border-gray-200 p-4', className)}>{children}</div>
}

function Info({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs text-gray-400">{label}</dt>
      <dd className={clsx('text-gray-800', mono && 'font-mono text-xs')}>{value}</dd>
    </div>
  )
}

function SubScore({ label, value }: { label: string; value: string }) {
  return <div className="flex justify-between"><span>{label}</span><span className="font-medium text-gray-700">{Number(value).toFixed(0)}</span></div>
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-lg font-bold text-gray-900">{value}</p>
      <p className="text-xs text-gray-400">{label}</p>
    </div>
  )
}
