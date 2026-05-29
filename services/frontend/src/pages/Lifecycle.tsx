import StatCard from '../components/StatCard'

const DUMMY_DEVICES = [
  { hostname: 'core-sw-01',   model: 'Catalyst 9500',  eol: '2027-04-30', status: 'warning' },
  { hostname: 'dist-rtr-02',  model: 'ISR 4451',        eol: '2026-08-31', status: 'critical' },
  { hostname: 'access-sw-10', model: 'Catalyst 2960',   eol: '2025-12-31', status: 'critical' },
]

const STATUS_COLORS: Record<string, string> = {
  healthy:  'bg-green-100 text-green-700',
  warning:  'bg-yellow-100 text-yellow-700',
  critical: 'bg-red-100 text-red-700',
}

export default function Lifecycle() {
  const criticalCount = DUMMY_DEVICES.filter(d => d.status === 'critical').length
  const warningCount  = DUMMY_DEVICES.filter(d => d.status === 'warning').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-gray-900">Device Lifecycle</h1>
        <button className="px-4 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700">
          Import Lifecycle Data
        </button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <StatCard title="EOL Devices"   value={criticalCount} color="red"    subtitle="Already end-of-life" />
        <StatCard title="EOL Warning"   value={warningCount}  color="yellow" subtitle="Less than 12 months remaining" />
        <StatCard title="Healthy"       value={0}             color="green"  subtitle="No EOL risk" />
      </div>

      <div className="bg-white rounded-lg border border-gray-200">
        <div className="px-4 py-3 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-700">End-of-Life Devices</h2>
          <p className="text-xs text-gray-400 mt-0.5">Sample data — connect lifecycle engine for live tracking</p>
        </div>
        <table className="min-w-full text-sm">
          <thead className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
            <tr>
              <th className="px-4 py-2 text-left">Hostname</th>
              <th className="px-4 py-2 text-left">Model</th>
              <th className="px-4 py-2 text-left">EOL Date</th>
              <th className="px-4 py-2 text-left">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {DUMMY_DEVICES.map(d => (
              <tr key={d.hostname} className="hover:bg-gray-50">
                <td className="px-4 py-2 font-medium font-mono">{d.hostname}</td>
                <td className="px-4 py-2 text-gray-600">{d.model}</td>
                <td className="px-4 py-2">{d.eol}</td>
                <td className="px-4 py-2">
                  <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium capitalize ${STATUS_COLORS[d.status] ?? ''}`}>
                    {d.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
        <p className="text-sm text-blue-800 font-medium">Next step: Configure the CVE Engine</p>
        <p className="text-sm text-blue-600 mt-1">
          Add your NVD API key in Settings to enable real-time EOL tracking and CVE vulnerability matching.
        </p>
        <a href="/settings" className="inline-block mt-2 text-sm text-blue-700 font-medium hover:underline">
          Configure in Settings →
        </a>
      </div>
    </div>
  )
}
