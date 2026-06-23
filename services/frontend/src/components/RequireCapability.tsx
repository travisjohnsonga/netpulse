import { type ReactNode } from 'react'
import { useCapabilities } from '../store/authStore'
import NotAuthorized from './NotAuthorized'

/**
 * Route/section guard: renders `children` only if the user holds the required
 * capability (any-of, if an array is given), else a clean NotAuthorized view.
 * Convenience + a friendly dead-end for deep-links — the API 403 is still the
 * authoritative control.
 */
export default function RequireCapability({
  capability,
  children,
}: {
  capability: string | string[]
  children: ReactNode
}) {
  const caps = useCapabilities()
  const needed = Array.isArray(capability) ? capability : [capability]
  const allowed = needed.some((c) => caps.includes(c))
  return allowed ? <>{children}</> : <NotAuthorized />
}
