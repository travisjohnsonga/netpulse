import { useCallback, useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchCollectors, type Collector } from '../../api/client'
import Modal from '../../components/Modal'
import EmptyState from '../../components/EmptyState'
import { SectionHeader } from '../Settings'

const STATUS_BADGE: Record<Collector['status'], string> = {
  active: 'bg-green-100 text-green-700',
  pending: 'bg-yellow-100 text-yellow-700',
  offline: 'bg-red-100 text-red-700',
  revoked: 'bg-gray-100 text-gray-500',
}

function relative(ts: string | null): string {
  if (!ts) return 'Never'
  return new Date(ts).toLocaleString()
}

export default function Collectors() {
  const [items, setItems] = useState<Collector[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [registering, setRegistering] = useState(false)

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

      {error && <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800 mb-4">{error}</div>}

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
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
                <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                  <th className="px-5 py-3 font-medium">Name</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                  <th className="px-5 py-3 font-medium">Version</th>
                  <th className="px-5 py-3 font-medium">Remote IP</th>
                  <th className="px-5 py-3 font-medium">Last seen</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {items.map((c) => (
                  <tr key={c.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-medium text-gray-800">{c.name}</td>
                    <td className="px-5 py-3">
                      <span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', STATUS_BADGE[c.status])}>{c.status}</span>
                    </td>
                    <td className="px-5 py-3 text-gray-600">{c.version || '—'}</td>
                    <td className="px-5 py-3 text-gray-600 font-mono text-xs">{c.remote_ip || '—'}</td>
                    <td className="px-5 py-3 text-gray-600">{relative(c.last_seen_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {registering && <RegisterModal onClose={() => setRegistering(false)} />}
    </div>
  )
}

function RegisterModal({ onClose }: { onClose: () => void }) {
  return (
    <Modal
      title="Register Collector"
      onClose={onClose}
      footer={<button onClick={onClose} className="flex-1 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">Done</button>}
    >
      <div className="space-y-3 text-sm text-gray-600">
        <p>Generate a one-time registration token, then start the collector with it. NetPulse issues an mTLS certificate via OpenBao PKI and invalidates the token after first use.</p>
        <div className="bg-gray-900 text-gray-100 rounded-lg p-3 font-mono text-xs overflow-x-auto">
          docker run -d --name netpulse-collector \<br />
          &nbsp;&nbsp;-e NETPULSE_CLOUD_URL=https://cloud.netpulse.io \<br />
          &nbsp;&nbsp;-e NETPULSE_TOKEN=&lt;one-time-token&gt; \<br />
          &nbsp;&nbsp;netpulse/collector:latest
        </div>
        <p className="text-xs text-gray-400">Token generation requires the collector registration endpoint (backend in progress).</p>
      </div>
    </Modal>
  )
}
