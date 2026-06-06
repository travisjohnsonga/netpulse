import { Link } from 'react-router-dom'
import clsx from 'clsx'

interface Props {
  // Device id when the device is in inventory; null/undefined → plain text.
  deviceId?: number | null
  hostname: string
  // Optional tab to deep-link into on the device detail page (e.g. 'telemetry').
  tab?: string
  className?: string
}

// Renders a device hostname as a link to its detail page when the device is in
// inventory, otherwise as plain text (e.g. an LLDP neighbor we don't manage).
export default function DeviceLink({ deviceId, hostname, tab, className }: Props) {
  if (!deviceId) {
    return <span className={className}>{hostname}</span>
  }
  const to = tab ? `/devices/${deviceId}?tab=${tab}` : `/devices/${deviceId}`
  return (
    <Link to={to} className={clsx('text-blue-600 dark:text-blue-400 hover:underline', className)}>
      {hostname}
    </Link>
  )
}
