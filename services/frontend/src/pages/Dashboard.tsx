import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import type { EChartsOption } from 'echarts'
import StatCard from '../components/StatCard'
import EmptyState from '../components/EmptyState'
import { fetchDevices, fetchAlerts, type Device, type Alert } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import clsx from 'clsx'

const SEVERITY_COLORS: Record<string, string> = {
  critical: 'bg-red-100 text-red-700',
  high: 'bg-orange-100 text-orange-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-blue-100 text-blue-700',
}

// Dummy chart data for placeholder charts
const now = Date.now()
const timeLabels = Array.from({ length: 12 }, (_, i) =>
  new Date(now - (11 - i) * 5 * 60 * 1000).toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
  }),
)

const deviceStatusChartOption: EChartsOption = {
  title: { text: 'Device Status Over Time', textStyle: { fontSize: 14, fontWeight: 600 } },
  tooltip: { trigger: 'axis' },
  legend: { data: ['Active', 'Unreachable'], bottom: 0 },
  grid: { left: 40, right: 20, top: 40, bottom: 40 },
  xAxis: { type: 'category', data: timeLabels, axisLabel: { fontSize: 11 } },
  yAxis: { type: 'value', minInterval: 1 },
  series: [
    {
      name: 'Active',
      type: 'line',
      smooth: true,
      data: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      itemStyle: { color: '#22c55e' },
      areaStyle: { opacity: 0.1 },
    },
    {
      name: 'Unreachable',
      type: 'line',
      smooth: true,
      data: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
      itemStyle: { color: '#ef4444' },
      areaStyle: { opacity: 0.1 },
    },
  ],
}

const topTalkersChartOption: EChartsOption = {
  title: { text: 'Top Talkers by Bytes', textStyle: { fontSize: 14, fontWeight: 600 } },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  grid: { left: 100, right: 20, top: 40, bottom: 30 },
  xAxis: { type: 'value', axisLabel: { formatter: (v: number) => `${v}GB` } },
  yAxis: {
    type: 'category',
    data: ['No data yet', '', '', '', ''],
    axisLabel: { fontSize: 11 },
  },
  series: [
    {
      name: 'Bytes',
      type: 'bar',
      data: [0, 0, 0, 0, 0],
      itemStyle: { color: '#3b82f6', borderRadius: [0, 4, 4, 0] },
    },
  ],
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [devices, setDevices] = useState<Device[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const { connected, lastMessage } = useWebSocket('/ws/telemetry/')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    Promise.allSettled([fetchDevices(), fetchAlerts()])
      .then(([devResult, alertResult]) => {
        if (cancelled) return
        if (devResult.status === 'fulfilled') setDevices(devResult.value.results)
        if (alertResult.status === 'fulfilled') setAlerts(alertResult.value)
        setLoading(false)
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not reach the API. Check that the backend is running.')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [])

  // Live message indicator
  useEffect(() => {
    if (lastMessage) {
      // In production this would update charts/stats
    }
  }, [lastMessage])

  const activeAlerts = alerts.filter((a) => a.state === 'firing')
  const criticalCount = activeAlerts.filter((a) => a.severity === 'critical').length
  const recentAlerts = alerts.slice(0, 5)

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-gray-500">Loading dashboard...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-sm text-gray-500 mt-0.5">Network overview at a glance</p>
        </div>
        <div className="flex items-center gap-2">
          {connected && (
            <span className="flex items-center gap-1.5 text-xs font-medium text-green-600 bg-green-50 px-2.5 py-1 rounded-full border border-green-200">
              <span className="w-1.5 h-1.5 bg-green-500 rounded-full animate-pulse" />
              Live
            </span>
          )}
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="bg-yellow-50 border border-yellow-200 rounded-lg px-4 py-3 text-sm text-yellow-800">
          {error}
        </div>
      )}

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Devices"
          value={devices.length}
          subtitle="managed devices"
          color="blue"
          action={devices.length === 0 ? { label: 'Add a device', href: '/devices' } : undefined}
        />
        <StatCard
          title="Active Alerts"
          value={activeAlerts.length}
          subtitle={`${criticalCount} critical`}
          color={criticalCount > 0 ? 'red' : activeAlerts.length > 0 ? 'yellow' : 'green'}
          action={{ label: 'View alerts', href: '/alerts' }}
        />
        <StatCard
          title="CVEs"
          value={0}
          subtitle="no data yet"
          color="yellow"
          action={{ label: 'Configure CVE feed', href: '/settings' }}
        />
        <StatCard
          title="Flows/sec"
          value="—"
          subtitle="awaiting NetFlow data"
          color="blue"
        />
      </div>

      {/* No devices empty state */}
      {devices.length === 0 && (
        <div className="bg-white rounded-lg shadow-sm border border-gray-200">
          <EmptyState
            title="No devices yet"
            description="Add your first network device to start seeing telemetry, alerts, and health data on this dashboard."
            action={{ label: 'Add Your First Device', onClick: () => navigate('/devices') }}
            icon="📡"
          />
        </div>
      )}

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <ReactECharts
            option={deviceStatusChartOption}
            style={{ height: 240 }}
            opts={{ renderer: 'svg' }}
          />
        </div>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
          <ReactECharts
            option={topTalkersChartOption}
            style={{ height: 240 }}
            opts={{ renderer: 'svg' }}
          />
        </div>
      </div>

      {/* Recent alerts table */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200">
        <div className="px-5 py-4 border-b border-gray-200 flex items-center justify-between">
          <h2 className="font-semibold text-gray-800">Recent Alerts</h2>
          <a href="/alerts" className="text-sm text-blue-600 hover:text-blue-800">
            View all
          </a>
        </div>
        {recentAlerts.length === 0 ? (
          <EmptyState
            title="No active alerts"
            description="Your network is healthy. Alerts will appear here when triggered."
            icon="✅"
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 text-gray-500 text-left">
                  <th className="px-5 py-3 font-medium">Severity</th>
                  <th className="px-5 py-3 font-medium">Rule</th>
                  <th className="px-5 py-3 font-medium">Device</th>
                  <th className="px-5 py-3 font-medium">Fired At</th>
                  <th className="px-5 py-3 font-medium">State</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {recentAlerts.map((alert) => (
                  <tr key={alert.id} className="hover:bg-gray-50">
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          SEVERITY_COLORS[alert.severity] ?? 'bg-gray-100 text-gray-600',
                        )}
                      >
                        {alert.severity}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-gray-800">{alert.rule_name}</td>
                    <td className="px-5 py-3 text-gray-600">{alert.device}</td>
                    <td className="px-5 py-3 text-gray-500">
                      {new Date(alert.fired_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={clsx(
                          'px-2 py-0.5 rounded-full text-xs font-medium capitalize',
                          alert.state === 'firing'
                            ? 'bg-red-100 text-red-700'
                            : alert.state === 'acknowledged'
                              ? 'bg-yellow-100 text-yellow-700'
                              : 'bg-green-100 text-green-700',
                        )}
                      >
                        {alert.state}
                      </span>
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
