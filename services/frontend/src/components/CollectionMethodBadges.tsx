import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { fetchCollectionStatus, type CollectionStatus } from '../api/client'

/**
 * Shows HOW a device's telemetry is currently being collected — gNMI streaming
 * and/or SNMP polling — from GET /devices/{id}/collection-status/.
 *
 * `variant="badges"` renders compact pills (device header); `variant="bar"`
 * renders a status box (Telemetry tab). Both auto-refresh every 60s.
 */
function relAgo(seconds: number | null): string {
  if (seconds == null) return ''
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

function everyLabel(seconds: number): string {
  if (seconds % 3600 === 0) return `every ${seconds / 3600}h`
  if (seconds % 60 === 0) return `every ${seconds / 60}m`
  return `every ${seconds}s`
}

function snmpVersionLabel(version: string | null): string {
  if (!version) return 'SNMP'
  return `SNMP${version}` // "v3" → "SNMPv3", "v2c" → "SNMPv2c"
}

function useCollectionStatus(deviceId: number): CollectionStatus | null {
  const [status, setStatus] = useState<CollectionStatus | null>(null)
  useEffect(() => {
    let cancelled = false
    const load = () => fetchCollectionStatus(deviceId)
      .then((s) => { if (!cancelled) setStatus(s) })
      .catch(() => {})
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [deviceId])
  return status
}

const badgeCls = 'inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium'

export function CollectionMethodBadges({ deviceId }: { deviceId: number }) {
  const status = useCollectionStatus(deviceId)
  if (!status) return null

  const { gnmi, snmp } = status

  if (!status.any_active) {
    return (
      <span
        className={clsx(badgeCls, 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/40 dark:text-yellow-300')}
        title={'No telemetry data received.\nConfigure in Settings → Telemetry'}
      >
        ⚠️ No telemetry
      </span>
    )
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      {gnmi.active && (
        <span
          className={clsx(badgeCls, 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300')}
          title={[
            'Streaming telemetry active',
            `Last message: ${relAgo(gnmi.last_seen_seconds_ago)}`,
            `${gnmi.metrics_per_push ?? '?'} metrics/push · ${everyLabel(gnmi.interval_seconds)}`,
          ].join('\n')}
        >
          📡 gNMI
        </span>
      )}
      {snmp.active && (
        <span
          className={clsx(badgeCls, 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300')}
          title={[
            'SNMP polling active',
            `Last poll: ${relAgo(snmp.last_poll_seconds_ago)} · ${everyLabel(snmp.interval_seconds)}`,
            `Version: ${snmpVersionLabel(snmp.version)}`,
          ].join('\n')}
        >
          📊 SNMP
        </span>
      )}
    </span>
  )
}

export function CollectionMethodBar({ deviceId }: { deviceId: number }) {
  const status = useCollectionStatus(deviceId)
  if (!status) return null

  const { gnmi, snmp } = status

  if (!status.any_active) {
    return (
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-900/50 bg-yellow-50 dark:bg-yellow-900/20 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-300">
        ⚠️ No telemetry received — configure in ⚙ Settings → Telemetry Configuration
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 px-3 py-2 text-xs space-y-1">
      {gnmi.active && (
        <div className="text-gray-700 dark:text-gray-300">
          <span className="text-green-600 dark:text-green-400 font-medium">📡 gNMI streaming</span>
          {' — '}
          {gnmi.metrics_per_push != null ? `${gnmi.metrics_per_push} metrics/push · ` : ''}
          {everyLabel(gnmi.interval_seconds)} · last {relAgo(gnmi.last_seen_seconds_ago)}
        </div>
      )}
      {snmp.active && (
        <div className="text-gray-700 dark:text-gray-300">
          <span className="text-blue-600 dark:text-blue-400 font-medium">📊 SNMP polling</span>
          {' — '}
          {snmpVersionLabel(snmp.version)} · {everyLabel(snmp.interval_seconds)} · last {relAgo(snmp.last_poll_seconds_ago)}
        </div>
      )}
    </div>
  )
}
