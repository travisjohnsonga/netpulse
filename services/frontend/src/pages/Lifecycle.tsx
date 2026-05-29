import StatCard from '../components/StatCard'
import clsx from 'clsx'

interface LifecycleRow {
  device: string
  platform: string
  vendor: string
  eol_date: string
  status: 'eol' | 'warning' | 'healthy'
}

const DUMMY_ROWS: LifecycleRow[] = [
  {
    device: 'Example: core-switch-01',
    platform: 'IOS-XE',
    vendor: 'Cisco',
    eol_date: '2024-09-30',
    status: 'eol',
  },
  {
    device: 'Example: dist-router-02',
    platform: 'IOS-XR',
    vendor: 'Cisco',
    eol_date: '2026-01-15',
    status: 'warning',
  },
  {
    device: 'Example: access-sw-05',
    platform: 'NX-OS',
    vendor: 'Cisco',
    eol_date: '2028-06-30',
    status: 'healthy',
  },
]

const STATUS_BADGE: Record<LifecycleRow['status'], string> = {
  eol: 'bg-red-100 text-red-700',
  warning: 'bg-yellow-100 text-yellow-700',
  healthy: 'bg-green-100 text-green-700',
}

const STATUS_LABEL: Record<LifecycleRow['status'], string> = {
  eol: 'End of Life',
  warning: 'EOL < 12 mo',
  healthy: 'Healthy',
}

export default function Lifecycle() {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Device Lifecycle</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            Track end-of-life and end-of-support dates across your inventory
          </p>
        </div>
        <button className="px-4 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
          Import Lifecycle Data
        </button>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatCard
          title="EOL Devices"
          value={0}
          subtitle="past end-of-life date"
          color="red"
        />
        <StatCard
          title="EOL Warning"
          value={0}
          subtitle="end of life within 12 months"
          color="yellow"
        />
        <StatCard
          title="Healthy"
          value={0}
          subtitle="supported for 12+ months"
          color="green"
        />
      </div>

      {/* Setup info */}
      <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 flex items-start gap-3">
        <span className="text-xl flex-shrink-0 mt-0.5">📅</span>
        <div>
          <p className="font-medium text-amber-900 text-sm">Lifecycle data not yet configured</p>
          <p className="text-xs text-amber-700 mt-0.5">
            The lifecycle engine tracks Cisco, Juniper, Arista, and other vendor EOS/EOL dates.
            Import from a CSV, connect to Cisco DNA Center, or let the lifecycle engine auto-populate
            based on your device inventory.
          </p>
        </div>
      </div>

      {/* Table (with dummy illustrative rows) */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">Device Lifecycle Table</h2>
          <span className="text-xs text-gray-400 bg-gray-100 px-2 py-1 rounded-full">
            Illustrative data
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 text-gray-500 text-left border-b border-gray-200">
                <th className="px-5 py-3 font-medium">Device</th>
                <th className="px-5 py-3 font-medium">Platform</th>
                <th className="px-5 py-3 font-medium">Vendor</th>
                <th className="px-5 py-3 font-medium">EOL Date</th>
                <th className="px-5 py-3 font-medium">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {DUMMY_ROWS.map((row) => (
                <tr key={row.device} className="hover:bg-gray-50 opacity-60">
                  <td className="px-5 py-3 font-medium text-gray-800 italic">{row.device}</td>
                  <td className="px-5 py-3 text-gray-600">{row.platform}</td>
                  <td className="px-5 py-3 text-gray-600">{row.vendor}</td>
                  <td className="px-5 py-3 text-gray-600 text-xs font-mono">{row.eol_date}</td>
                  <td className="px-5 py-3">
                    <span
                      className={clsx(
                        'px-2 py-0.5 rounded-full text-xs font-medium',
                        STATUS_BADGE[row.status],
                      )}
                    >
                      {STATUS_LABEL[row.status]}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="px-5 py-3 border-t border-gray-100 bg-gray-50">
          <p className="text-xs text-gray-400 italic">
            Rows above are illustrative. Real lifecycle data will appear after import or auto-population.
          </p>
        </div>
      </div>

      {/* Action cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {[
          {
            icon: '📤',
            title: 'Import from CSV',
            description: 'Upload a spreadsheet with device EOL dates',
            action: 'Import CSV',
          },
          {
            icon: '🔗',
            title: 'Cisco DNA Center',
            description: 'Pull lifecycle data directly from DNAC',
            action: 'Connect',
          },
          {
            icon: '🤖',
            title: 'Auto-populate',
            description: 'Match devices to vendor lifecycle databases',
            action: 'Configure',
          },
        ].map((card) => (
          <div
            key={card.title}
            className="bg-white rounded-lg shadow-sm border border-gray-200 p-5 flex flex-col gap-3"
          >
            <span className="text-2xl">{card.icon}</span>
            <div>
              <p className="font-semibold text-gray-800 text-sm">{card.title}</p>
              <p className="text-xs text-gray-500 mt-0.5">{card.description}</p>
            </div>
            <button className="mt-auto px-3 py-2 border border-gray-300 text-gray-700 rounded-lg text-sm font-medium hover:bg-gray-50 transition-colors">
              {card.action}
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
