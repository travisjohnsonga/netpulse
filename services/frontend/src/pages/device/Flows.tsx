import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import FlowsTable from '../../components/FlowsTable'
import { fetchFlows, fetchFlowSummary, type DeviceDetail } from '../../api/client'
import { fmtBytes } from '../../lib/bytes'

const WINDOWS = ['1h', '6h', '24h', '7d'] as const
type Window = (typeof WINDOWS)[number]

// Device "Flows" tab — flows exported by this device (exporter_ip = device IP).
export default function Flows({ device }: { device: DeviceDetail }) {
  const [window, setWindow] = useState<Window>('1h')
  const deviceId = String(device.id)

  const summaryQ = useQuery({
    queryKey: ['device-flow-summary', deviceId, window],
    queryFn: () => fetchFlowSummary({ window, device_id: deviceId }),
  })
  const flowsQ = useQuery({
    queryKey: ['device-flows', deviceId, window],
    queryFn: () => fetchFlows({ window, device_id: deviceId, limit: '200' }),
  })

  const summary = summaryQ.data

  return (
    <div className="bg-white dark:bg-gray-800 rounded-lg shadow-sm border border-gray-200 dark:border-gray-700">
      <div className="flex items-center justify-between flex-wrap gap-3 px-4 py-3 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center gap-4 text-sm">
          <h3 className="font-semibold text-gray-800 dark:text-gray-100">Flows — {device.hostname}</h3>
          {summary && (
            <span className="text-gray-500 dark:text-gray-400">
              {summary.total_flows.toLocaleString()} flows · {fmtBytes(summary.total_bytes)} · {summary.unique_dst_ips} destinations
            </span>
          )}
        </div>
        <div className="flex gap-1">
          {WINDOWS.map((w) => (
            <button
              key={w}
              onClick={() => setWindow(w)}
              className={clsx(
                'px-2.5 py-1 text-xs rounded-md border',
                window === w
                  ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
                  : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50',
              )}
            >
              {w}
            </button>
          ))}
        </div>
      </div>

      <FlowsTable rows={flowsQ.data?.results ?? []} loading={flowsQ.isLoading} maxHeight="max-h-[32rem]" />

      <div className="px-4 py-3 border-t border-gray-200 dark:border-gray-700 text-xs text-gray-500 dark:text-gray-400">
        {(flowsQ.data?.results.length ?? 0).toLocaleString()} of {(flowsQ.data?.count ?? 0).toLocaleString()} flows
        {!flowsQ.isLoading && (flowsQ.data?.count ?? 0) === 0 && (
          <span className="ml-2">— this device may not be configured as a NetFlow/sFlow exporter.</span>
        )}
      </div>
    </div>
  )
}
