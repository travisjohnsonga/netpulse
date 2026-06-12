import clsx from 'clsx'
import type { Site } from '../api/client'

type Counts = Pick<Site, 'device_count' | 'devices_up' | 'devices_down' | 'devices_unknown'>

/**
 * Compact up/down/unknown device-count indicators for a site. Shows green ↑
 * (reachable & active), red ↓ (down/unreachable) and a muted ? (unknown).
 * Falls back to a neutral total when no device sits in any bucket, and a muted
 * "— no devices" when the site is empty. `compact` shrinks it for tight spots
 * like the global site selector.
 */
export default function SiteDeviceStatus({
  site,
  compact = false,
  className,
}: {
  site: Counts
  compact?: boolean
  className?: string
}) {
  const { device_count, devices_up, devices_down, devices_unknown } = site
  const size = compact ? 'text-[11px]' : 'text-sm'

  if (!device_count) {
    if (compact) return null
    return <span className={clsx('text-gray-400 dark:text-gray-500', size, className)}>— no devices</span>
  }

  const bucketed = devices_up + devices_down + devices_unknown
  return (
    <span className={clsx('inline-flex items-center gap-2 font-medium tabular-nums', size, className)}>
      {devices_up > 0 && (
        <span className="inline-flex items-center gap-0.5 text-green-600 dark:text-green-400" title={`${devices_up} up`}>
          <span aria-hidden>↑</span>{devices_up}
        </span>
      )}
      {devices_down > 0 && (
        <span className="inline-flex items-center gap-0.5 text-red-600 dark:text-red-400" title={`${devices_down} down`}>
          <span aria-hidden>↓</span>{devices_down}
        </span>
      )}
      {devices_unknown > 0 && (
        <span className="inline-flex items-center gap-0.5 text-gray-400 dark:text-gray-500" title={`${devices_unknown} unknown`}>
          <span aria-hidden>?</span>{devices_unknown}
        </span>
      )}
      {bucketed === 0 && (
        <span className="text-gray-500 dark:text-gray-400" title={`${device_count} devices`}>{device_count}</span>
      )}
    </span>
  )
}
