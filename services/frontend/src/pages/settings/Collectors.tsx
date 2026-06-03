import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchCollectors, fetchSites, updateCollector, type Collector, type Site } from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'

const STATUS_BADGE: Record<Collector['status'], string> = {
  active: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  pending: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400',
  offline: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
  revoked: 'bg-gray-100 text-gray-500 dark:bg-gray-700 dark:text-gray-400',
}

const TYPE_BADGE: Record<Collector['collector_type'], string> = {
  local: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400',
  remote: 'bg-sky-100 text-sky-700 dark:bg-sky-900/30 dark:text-sky-400',
}

function relative(ts: string | null): string {
  if (!ts) return 'Never'
  return new Date(ts).toLocaleString()
}

function HealthDot({ c }: { c: Collector }) {
  const healthy = c.is_healthy
  return (
    <span className={clsx('inline-flex items-center gap-1.5 text-xs font-medium',
      healthy ? 'text-green-600 dark:text-green-400' : 'text-gray-500 dark:text-gray-400')}>
      <span className={clsx('w-2 h-2 rounded-full', healthy ? 'bg-green-500' : 'bg-gray-400')} />
      {healthy ? 'Healthy' : 'No heartbeat'}
    </span>
  )
}

export default function Collectors() {
  const [items, setItems] = useState<Collector[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [registering, setRegistering] = useState(false)
  const [selected, setSelected] = useState<Collector | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    fetchCollectors()
      .then((data) => { setItems(data); setError(null) })
      .catch(() => setError('Failed to load collectors.'))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div>
      <SectionHeader
        title="Collectors"
        description="On-prem collectors and remote pollers connected over outbound mTLS."
        action={<button onClick={() => setRegistering(true)} className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">+ Register Collector</button>}
      />

      {error && <div className="bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-yellow-800 dark:text-yellow-400 mb-4">{error}</div>}

      <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
        {loading ? (
          <div className="flex items-center justify-center py-16">
            <div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          </div>
        ) : items.length === 0 ? (
          <EmptyState title="No collectors registered" description="Register a collector to forward telemetry and poll devices from a remote site." action={{ label: 'Register Collector', onClick: () => setRegistering(true) }} icon="📡" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 dark:bg-gray-900/50 text-gray-500 dark:text-gray-400 text-left border-b border-gray-200 dark:border-gray-700">
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Type</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Health</th>
                  <th className="px-5 py-3 font-medium">Devices</th>
                  <th className="px-5 py-3 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {items.map((c) => (
                  <tr key={c.id} onClick={() => setSelected(c)} className="hover:bg-gray-50 dark:hover:bg-gray-700/50 cursor-pointer">
                    <td className="px-5 py-3 font-medium text-gray-800 dark:text-gray-100">
                      {c.name}{c.is_default && <span className="ml-2 text-xs text-gray-400">default</span>}
                    </td>
                    <td className="px-5 py-3">
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', TYPE_BADGE[c.collector_type])}>{c.collector_type === 'local' ? 'Local' : 'Remote'}</span>
                    </td>
                    <td className="px-5 py-3">
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[c.status])}>{c.status}</span>
                    </td>
                    <td className="px-5 py-3"><HealthDot c={c} /></td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{c.device_count ?? 0}</td>
                    <td className="px-5 py-3 text-gray-600 dark:text-gray-400">{relative(c.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {registering && <RegisterModal onClose={() => setRegistering(false)} />}
      {selected && (
        <CollectorDetailModal
          collector={selected}
          onClose={() => setSelected(null)}
          onSaved={(c) => { setSelected(c); load() }}
        />
      )}
    </div>
  )
}

function CollectorDetailModal({ collector, onClose, onSaved }: {
  collector: Collector
  onClose: () => void
  onSaved: (c: Collector) => void
}) {
  const [sites, setSites] = useState<Site[]>([])
  const [siteId, setSiteId] = useState<number | ''>(collector.site ?? '')
  const [saving, setSaving] = useState(false)

  useEffect(() => { fetchSites().then(setSites).catch(() => {}) }, [])

  const saveSite = async (value: number | '') => {
    setSiteId(value); setSaving(true)
    try { onSaved(await updateCollector(collector.id, { site: value === '' ? null : Number(value) })) }
    finally { setSaving(false) }
  }

  const caps = Object.entries(collector.capabilities || {}).filter(([, v]) => v).map(([k]) => k)

  return (
    <Modal title={collector.name} onClose={onClose} size="lg"
      footer={<button onClick={onClose} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Done</button>}>
      <div className="space-y-4 text-sm">
        <div className="grid grid-cols-2 gap-x-4 gap-y-2">
          <Info label="Type" value={collector.collector_type === 'local' ? 'Local Server' : 'Remote Agent'} />
          <Info label="Status" value={collector.status} />
          <Info label="Hostname" value={collector.hostname || '—'} />
          <Info label="IP address" value={collector.collector_ip || collector.remote_ip || '—'} />
          <Info label="Version" value={collector.version || '—'} />
          <Info label="Assigned devices" value={String(collector.device_count ?? 0)} />
          <Info label="Last heartbeat" value={relative(collector.last_seen_at)} />
          <Info label="Health" value={collector.is_healthy ? 'Healthy' : 'No recent heartbeat'} />
        </div>

        <div>
          <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1">Site</label>
          <select
            className="w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
            value={siteId} disabled={saving}
            onChange={(e) => saveSite(e.target.value === '' ? '' : Number(e.target.value))}
          >
            <option value="">— No site —</option>
            {sites.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          {saving && <p className="text-xs text-gray-400 mt-1">Saving…</p>}
        </div>

        <div>
          <h4 className="text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">Capabilities</h4>
          {caps.length === 0 ? (
            <p className="text-xs text-gray-400">None reported.</p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {caps.map((k) => (
                <span key={k} className="px-2 py-0.5 rounded-md text-xs bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300 uppercase">{k}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    </Modal>
  )
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-gray-400 dark:text-gray-500">{label}</dt><dd className="text-gray-800 dark:text-gray-100 capitalize">{value}</dd></div>
}

function RegisterModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      title="Register Collector"
      onClose={onClose}
      footer={<button onClick={onClose} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Done</button>}
    >
      <div className="space-y-3 text-sm text-gray-600 dark:text-gray-400">
        <p>Generate a one-time registration token, then start the collector with it. NetPulse issues an mTLS certificate via OpenBao PKI and invalidates the token after first use.</p>
        <div className="bg-gray-900 text-gray-100 rounded-lg p-3 font-mono text-xs overflow-x-auto">
          docker run -d --name netpulse-collector \<br />
          &nbsp;&nbsp;-e NETPULSE_CLOUD_URL=https://cloud.netpulse.io \<br />
          &nbsp;&nbsp;-e NETPULSE_TOKEN=&lt;one-time-token&gt; \<br />
          &nbsp;&nbsp;netpulse/collector:latest
        </div>
        <p className="text-xs text-gray-400 dark:text-gray-500">Token generation requires the collector registration endpoint (backend in progress).</p>
      </div>
    </Modal>
  )
}
