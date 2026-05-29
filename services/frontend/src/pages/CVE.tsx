import { useNavigate } from 'react-router-dom'
import StatCard from '../components/StatCard'

interface CVERow {
  cve_id: string
  severity: string
  title: string
  affected_devices: number
  published: string
  patched: boolean
}

// Placeholder rows to illustrate the table schema
const PLACEHOLDER_ROWS: CVERow[] = []

export default function CVE() {
  const navigate = useNavigate()

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">CVE Intelligence</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Track vulnerabilities affecting your network devices
          </p>
        </div>
        <button
          onClick={() => navigate('/settings')}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Configure NVD API Key
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          title="Critical CVEs"
          value={0}
          subtitle="no feed configured"
          color="red"
        />
        <StatCard
          title="Affected Devices"
          value={0}
          subtitle="no vulnerabilities detected"
          color="yellow"
        />
        <StatCard
          title="Patched"
          value={0}
          subtitle="nothing to patch yet"
          color="green"
        />
      </div>

      {/* Setup banner */}
      <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 flex flex-col sm:flex-row items-start sm:items-center gap-4">
        <div className="flex-shrink-0 w-10 h-10 bg-blue-100 rounded-lg flex items-center justify-center text-xl">
          🔑
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-medium text-blue-900 text-sm">NVD API key required</p>
          <p className="text-xs text-blue-700 mt-0.5">
            Configure your NVD API key and Cisco PSIRT credentials in Settings to enable CVE
            intelligence. The CVE engine will then automatically correlate vulnerabilities against
            your device inventory.
          </p>
        </div>
        <button
          onClick={() => navigate('/settings')}
          className="flex-shrink-0 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors"
        >
          Go to Settings
        </button>
      </div>

      {/* CVE table placeholder */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">Vulnerability Feed</h2>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">
            Waiting for CVE engine
          </span>
        </div>
        {PLACEHOLDER_ROWS.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
            <span className="text-5xl mb-4" role="img" aria-label="shield">🛡</span>
            <h3 className="text-lg font-semibold text-gray-700 mb-2">
              CVE data will appear here once the CVE engine is running
            </h3>
            <p className="text-sm text-gray-500 max-w-sm mb-6">
              The CVE engine pulls from NVD and Cisco PSIRT, then correlates vulnerabilities
              against your device platform and software versions.
            </p>
            <div className="flex flex-col sm:flex-row gap-3">
              <button
                onClick={() => navigate('/settings')}
                className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
              >
                Configure API Keys
              </button>
              <button
                onClick={() => navigate('/devices')}
                className="px-5 py-2.5 border border-gray-300 text-gray-700 hover:bg-gray-50 rounded-lg text-sm font-medium transition-colors"
              >
                Check Device Inventory
              </button>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                  <th className="px-5 py-3 font-medium">CVE ID</th>
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Title</th>
                  <th className="px-5 py-3 font-medium">Affected Devices</th>
                  <th className="px-5 py-3 font-medium">Published</th>
                  <th className="px-5 py-3 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {PLACEHOLDER_ROWS.map((row) => (
                  <tr key={row.cve_id} className="hover:bg-gray-50">
                    <td className="px-5 py-3 font-mono text-xs text-blue-600">{row.cve_id}</td>
                    <td className="px-5 py-3">{row.severity}</td>
                    <td className="px-5 py-3 text-gray-800">{row.title}</td>
                    <td className="px-5 py-3 text-gray-600">{row.affected_devices}</td>
                    <td className="px-5 py-3 text-gray-500 text-xs">{row.published}</td>
                    <td className="px-5 py-3">
                      {row.patched ? (
                        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                          Patched
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-700">
                          Unpatched
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
