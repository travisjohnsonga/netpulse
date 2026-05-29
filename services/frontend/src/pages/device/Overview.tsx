import { useEffect, useState } from 'react'
import clsx from 'clsx'
import {
  fetchDeviceRiskScore, fetchDeviceAlerts,
  type DeviceDetail, type RiskScore, type AlertEvent,
} from '../../api/client'
import Gauge from '../../components/Gauge'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-blue-100 text-blue-700',
  info: 'bg-gray-100 text-gray-600',
}

export default function Overview({ device, onTab }: { device: DeviceDetail; onTab: (t: string) => void }) {
  const [risk, setRisk] = useState<RiskScore | null>(null)
  const [alerts, setAlerts] = useState<AlertEvent[]>([])
  const [alertsLoaded, setAlertsLoaded] = useState(false)

  useEffect(() => {
    fetchDeviceRiskScore(device.id).then(setRisk).catch(() => setRisk(null))
    fetchDeviceAlerts(device.id, device.hostname)
      .then((a) => setAlerts(a.slice(0, 5)))
      .catch(() => setAlerts([]))
      .finally(() => setAlertsLoaded(true))
  }, [device.id, device.hostname])

  const reachable = device.status === 'active'

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Device info */}
      <Card className="lg:col-span-2">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Device Information</h3>
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
              {device.credentials.length} configured →
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
        <p className="text-xs text-gray-400 mt-3">Live metrics appear once the telemetry pipeline reports for this device.</p>
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

      {/* Recent config changes (illustrative — config API pending) */}
      <Card>
        <h3 className="text-sm font-semibold text-gray-800 mb-3">Recent Config Changes</h3>
        <ul className="space-y-2 text-sm">
          {[
            { when: '2d ago', who: 'config-backup', what: 'Running config snapshot' },
            { when: '5d ago', who: 'dana', what: 'interface Gi0/1 description updated' },
            { when: '12d ago', who: 'config-backup', what: 'Startup config saved' },
          ].map((c, i) => (
            <li key={i} className="flex items-center gap-2">
              <span className="text-gray-700 truncate">{c.what}</span>
              <span className="ml-auto text-xs text-gray-400 whitespace-nowrap">{c.who} · {c.when}</span>
            </li>
          ))}
        </ul>
        <button onClick={() => onTab('configuration')} className="mt-3 text-xs font-medium text-blue-600 hover:text-blue-800">View configuration →</button>
      </Card>
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
