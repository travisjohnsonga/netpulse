import { Link } from 'react-router-dom'
import clsx from 'clsx'

interface Props {
  ip: string
  className?: string
  // Optional display text (e.g. a resolved hostname); defaults to the IP.
  label?: string
}

// Renders an IP address as a monospace link to the IP/MAC lookup page.
export default function IPLink({ ip, className, label }: Props) {
  if (!ip) return <span className={className}>—</span>
  return (
    <Link
      to={`/network/lookup?q=${encodeURIComponent(ip)}`}
      className={clsx('font-mono text-blue-600 dark:text-blue-400 hover:underline', className)}
    >
      {label ?? ip}
    </Link>
  )
}
