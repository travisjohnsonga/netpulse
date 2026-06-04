import { type DeviceRole } from '../api/client'

/** A coloured pill showing a device's role. Renders nothing when role is null. */
export default function RoleBubble({ role, className }: { role?: DeviceRole | null; className?: string }) {
  if (!role) return null
  return (
    <span
      style={{
        backgroundColor: role.color + '20',
        color: role.color,
        border: `1px solid ${role.color}`,
      }}
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium whitespace-nowrap ${className ?? ''}`}
      title={role.description || role.name}
    >
      {role.icon && <span aria-hidden>{role.icon}</span>}
      {role.name}
    </span>
  )
}

/** Small solid colour dot — used in role dropdowns and the roles settings table. */
export function RoleDot({ color, className }: { color: string; className?: string }) {
  return (
    <span
      style={{ backgroundColor: color }}
      className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${className ?? ''}`}
    />
  )
}
