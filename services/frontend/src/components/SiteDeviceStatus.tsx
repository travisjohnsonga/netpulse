import clsx from 'clsx'
import type { Site } from '../api/client'

/**
 * Compact up/down(/unknown) count indicators in the device-status visual style:
 * green ↑ (up), red ↓ (down), muted ? (unknown). Falls back to a neutral total
 * when nothing sits in a bucket, and a muted empty label when the total is 0.
 * `compact` shrinks it for tight spots like the global site selector.
 */
export function CountStatus({
  total,
  up,
  down,
  unknown = 0,
  emptyLabel,
  compact = false,
  className,
}: {
  total: number
  up: number
  down: number
  unknown?: number
  emptyLabel: string
  compact?: boolean
  className?: string
}) {
  const size = compact ? 'text-[11px]' : 'text-sm'

  if (!total) {
    if (compact) return null
    return <span className={clsx('text-gray-400 dark:text-gray-500', size, className)}>{emptyLabel}</span>
  }

  const bucketed = up + down + unknown
  return (
    <span className={clsx('inline-flex items-center gap-2 font-medium tabular-nums', size, className)}>
      {up > 0 && (
        <span className="inline-flex items-center gap-0.5 text-green-600 dark:text-green-400" title={`${up} up`}>
          <span aria-hidden>↑</span>{up}
        </span>
      )}
      {down > 0 && (
        <span className="inline-flex items-center gap-0.5 text-red-600 dark:text-red-400" title={`${down} down`}>
          <span aria-hidden>↓</span>{down}
        </span>
      )}
      {unknown > 0 && (
        <span className="inline-flex items-center gap-0.5 text-gray-400 dark:text-gray-500" title={`${unknown} unknown`}>
          <span aria-hidden>?</span>{unknown}
        </span>
      )}
      {bucketed === 0 && (
        <span className="text-gray-500 dark:text-gray-400" title={`${total} total`}>{total}</span>
      )}
    </span>
  )
}

type DeviceCounts = Pick<Site, 'device_count' | 'devices_up' | 'devices_down' | 'devices_unknown'>

/** Device up/down/unknown indicators for a site. */
export default function SiteDeviceStatus({
  site,
  compact = false,
  className,
}: {
  site: DeviceCounts
  compact?: boolean
  className?: string
}) {
  return (
    <CountStatus
      total={site.device_count}
      up={site.devices_up}
      down={site.devices_down}
      unknown={site.devices_unknown}
      emptyLabel="— no devices"
      compact={compact}
      className={className}
    />
  )
}

type ServerCounts = Pick<Site, 'server_count' | 'servers_up' | 'servers_down'>

/** Server (agent) online/offline indicators for a site. */
export function SiteServerStatus({
  site,
  compact = false,
  className,
}: {
  site: ServerCounts
  compact?: boolean
  className?: string
}) {
  return (
    <CountStatus
      total={site.server_count}
      up={site.servers_up}
      down={site.servers_down}
      emptyLabel="— no servers"
      compact={compact}
      className={className}
    />
  )
}

type CheckCounts = Pick<Site, 'check_count' | 'checks_up' | 'checks_down'>

/** Service-check passing/failing indicators for a site. */
export function SiteCheckStatus({
  site,
  compact = false,
  className,
}: {
  site: CheckCounts
  compact?: boolean
  className?: string
}) {
  return (
    <CountStatus
      total={site.check_count}
      up={site.checks_up}
      down={site.checks_down}
      emptyLabel="— no checks"
      compact={compact}
      className={className}
    />
  )
}
