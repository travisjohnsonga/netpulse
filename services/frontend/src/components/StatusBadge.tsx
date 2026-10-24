import { badge } from '../lib/ui'

// One shared up/down status pill used by BOTH the Devices and Servers lists —
// green "Up" / red "Down", no duration inside (the "Last Change" column carries
// the time). Replaces the old Online/Offline (servers) and Active/Unreachable
// (devices) wording so both pages read identically.
// `maintenanceUntil` (ISO) overrides up/down with an amber "Maintenance" pill:
// the device's alerts are muted, so Down would be noise.
export default function StatusBadge({ up, maintenanceUntil }: { up: boolean; maintenanceUntil?: string | null }) {
  if (maintenanceUntil) {
    return (
      <span className={badge('warn')} title={`In maintenance until ${new Date(maintenanceUntil).toLocaleString()} (${up ? 'up' : 'down'})`}>
        Maintenance
      </span>
    )
  }
  return <span className={badge(up ? 'ok' : 'down')}>{up ? 'Up' : 'Down'}</span>
}
