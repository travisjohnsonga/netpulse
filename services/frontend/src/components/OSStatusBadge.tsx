import clsx from 'clsx'
import type { OSInventoryStatus } from '../api/client'

// Shared styling + emoji legend for OS-version compliance status, used by the
// OS Versions, Fleet Inventory, and device Compliance views.
export const OS_STATUS_META: Record<OSInventoryStatus, { icon: string; label: string; badge: string }> = {
  preferred:  { icon: '🟢', label: 'Preferred',  badge: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400' },
  approved:   { icon: '🟡', label: 'Approved',   badge: 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-400' },
  deprecated: { icon: '🟠', label: 'Deprecated', badge: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400' },
  prohibited: { icon: '🔴', label: 'Prohibited', badge: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400' },
  unknown:    { icon: '❓', label: 'Not in policy', badge: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300' },
}

export default function OSStatusBadge({ status, className }: { status: OSInventoryStatus; className?: string }) {
  const m = OS_STATUS_META[status] ?? OS_STATUS_META.unknown
  return (
    <span className={clsx('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium', m.badge, className)}>
      <span aria-hidden>{m.icon}</span>{m.label}
    </span>
  )
}
