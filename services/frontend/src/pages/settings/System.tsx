import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { checkInfraHealth, type InfraHealth } from '../../api/client'
import { SectionHeader } from '../Settings'
import TrustedCACerts from './TrustedCACerts'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

const RETENTION_TYPES = [
  { id: 'metrics', label: 'Telemetry metrics (InfluxDB)', def: '90' },
  { id: 'logs', label: 'Logs (OpenSearch)', def: '30' },
  { id: 'flows', label: 'NetFlow/sFlow records', def: '14' },
  { id: 'events', label: 'Alert events', def: '365' },
  { id: 'audit', label: 'Audit log', def: '730' },
]

const LICENSES = [
  'Django (BSD-3)', 'Django REST Framework (BSD-3)', 'React (MIT)',
  'Cytoscape.js (MIT)', 'Apache ECharts (Apache-2.0)', 'Tailwind CSS (MIT)',
  'OpenBao (MPL-2.0)', 'NATS (Apache-2.0)', 'Valkey (BSD-3)',
]

const AUDIT_SAMPLE = [
  { ts: '2026-05-29 19:40', user: 'you', action: 'Created credential profile "DC Core SNMPv3"' },
  { ts: '2026-05-29 18:12', user: 'dana', action: 'Enabled alert rule "High CPU on core devices"' },
  { ts: '2026-05-29 17:55', user: 'you', action: 'Approved discovered device 10.0.12.4' },
]

export default function System() {
  const [infra, setInfra] = useState<InfraHealth | null>(null)
  const [retention, setRetention] = useState<Record<string, string>>(
    Object.fromEntries(RETENTION_TYPES.map((r) => [r.id, r.def])),
  )

  useEffect(() => { checkInfraHealth().then(setInfra).catch(() => setInfra(null)) }, [])

  return (
    <div className="space-y-6">
      <SectionHeader title="System" description="Platform info, data retention, audit log and licenses." />

      {/* Platform info */}
      <section>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Platform</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <InfoCard label="Version" value="0.1.0-dev" />
          <InfoCard label="Last backup" value="—" />
          <InfoCard label="Services" value={infra ? `${Object.values(infra.services).filter(Boolean).length}/${Object.keys(infra.services).length} up` : '…'} />
          <InfoCard label="Environment" value="Development" />
        </div>
        {infra && (
          <div className="flex flex-wrap gap-2 mt-3">
            {Object.entries(infra.services).map(([svc, up]) => (
              <span key={svc} className={clsx('inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-md', up ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600')}>
                <span className={clsx('w-1.5 h-1.5 rounded-full', up ? 'bg-green-500' : 'bg-red-500')} />
                {svc}
              </span>
            ))}
          </div>
        )}
      </section>

      {/* Data retention */}
      <section>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Data retention</h3>
        <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
          {RETENTION_TYPES.map((r) => (
            <div key={r.id} className="flex items-center gap-4 px-5 py-3">
              <span className="flex-1 text-sm text-gray-700">{r.label}</span>
              <input
                className={`${inputCls} w-24`}
                type="number"
                value={retention[r.id]}
                onChange={(e) => setRetention((p) => ({ ...p, [r.id]: e.target.value }))}
              />
              <span className="text-xs text-gray-400 w-10">days</span>
            </div>
          ))}
        </div>
      </section>

      {/* Trusted CA certificates */}
      <TrustedCACerts />

      {/* Audit log */}
      <section>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">Audit log <span className="font-normal text-gray-400">(recent)</span></h3>
        <div className="bg-white rounded-lg border border-gray-200 divide-y divide-gray-100">
          {AUDIT_SAMPLE.map((a, i) => (
            <div key={i} className="flex items-center gap-4 px-5 py-2.5 text-sm">
              <span className="text-xs text-gray-400 font-mono w-36 shrink-0">{a.ts}</span>
              <span className="font-medium text-gray-700 w-16 shrink-0">{a.user}</span>
              <span className="text-gray-600 truncate">{a.action}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-gray-400 mt-2">Full audit log viewer (last 100 actions) lands with the audit backend.</p>
      </section>

      {/* About */}
      <section>
        <h3 className="text-sm font-semibold text-gray-800 mb-2">About & licenses</h3>
        <div className="bg-white rounded-lg border border-gray-200 p-4">
          <p className="text-sm text-gray-600 mb-3">NetPulse — push-first, open-source network intelligence platform.</p>
          <div className="flex flex-wrap gap-2">
            {LICENSES.map((l) => <span key={l} className="text-xs bg-gray-100 text-gray-600 px-2 py-1 rounded-md">{l}</span>)}
          </div>
        </div>
      </section>
    </div>
  )
}

function InfoCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-white rounded-lg border border-gray-200 p-4">
      <p className="text-xs text-gray-500">{label}</p>
      <p className="text-lg font-semibold text-gray-900 mt-0.5">{value}</p>
    </div>
  )
}
