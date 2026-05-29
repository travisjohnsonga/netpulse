import { useEffect, useMemo, useState } from 'react'
import clsx from 'clsx'
import { fetchDeviceCVEs, type DeviceDetail, type DeviceCVE } from '../../api/client'
import EmptyState from '../../components/EmptyState'

const SEVERITY_BADGE: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-blue-100 text-blue-700',
  none: 'bg-gray-100 text-gray-600',
}

const SEVERITY_ORDER = ['critical', 'high', 'medium', 'low', 'none']

function statusOf(cve: DeviceCVE): { label: string; cls: string } {
  if (cve.is_patched) return { label: 'MITIGATED', cls: 'bg-green-100 text-green-700' }
  if (cve.severity === 'none') return { label: 'NOT_APPLICABLE', cls: 'bg-gray-100 text-gray-500' }
  return { label: 'VULNERABLE', cls: 'bg-red-100 text-red-700' }
}

export default function CVE({ device }: { device: DeviceDetail }) {
  const [cves, setCves] = useState<DeviceCVE[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [severity, setSeverity] = useState('All')

  useEffect(() => {
    setLoading(true)
    fetchDeviceCVEs(device.id)
      .then((c) => { setCves(c); setError(null) })
      .catch(() => setError('Failed to load CVE exposure.'))
      .finally(() => setLoading(false))
  }, [device.id])

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    for (const x of cves) if (!x.is_patched) c[x.severity] = (c[x.severity] ?? 0) + 1
    return c
  }, [cves])

  const filtered = useMemo(
    () => cves.filter((c) => severity === 'All' || c.severity === severity)
      .sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity)),
    [cves, severity],
  )

  if (loading) return <div className="flex items-center justify-center py-16"><div className="w-6 h-6 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
  if (error) return <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">{error}</div>
  if (cves.length === 0) {
    return <div className="bg-white rounded-lg border border-gray-200"><EmptyState title="No known CVE exposure" description="No CVEs are currently associated with this device's platform and version." icon="🛡" /></div>
  }

  return (
    <div className="space-y-4">
      {/* Summary + filter */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          {SEVERITY_ORDER.filter((s) => counts[s]).map((s) => (
            <span key={s} className={clsx('text-xs font-medium px-2 py-1 rounded-md capitalize', SEVERITY_BADGE[s])}>
              {counts[s]} {s}
            </span>
          ))}
          {Object.keys(counts).length === 0 && <span className="text-xs text-green-600 font-medium">All known CVEs mitigated</span>}
        </div>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} className="px-3 py-2 border border-gray-300 rounded-lg text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500">
          {['All', ...SEVERITY_ORDER].map((s) => <option key={s} value={s}>{s === 'All' ? 'All severities' : s}</option>)}
        </select>
      </div>

      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                <th className="px-5 py-3 font-medium">CVE</th>
                <th className="px-5 py-3 font-medium">Severity</th>
                <th className="px-5 py-3 font-medium">CVSS</th>
                <th className="px-5 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {filtered.map((c) => {
                const st = statusOf(c)
                return (
                  <tr key={c.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-mono text-xs text-gray-800">{c.cve_id}</td>
                    <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium capitalize', SEVERITY_BADGE[c.severity])}>{c.severity}</span></td>
                    <td className="px-5 py-3 text-gray-600">{c.cvss_score ?? '—'}</td>
                    <td className="px-5 py-3"><span className={clsx('px-2 py-0.5 rounded-full text-xs font-medium', st.cls)}>{st.label}</span></td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
