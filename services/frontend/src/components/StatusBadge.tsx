import { badge } from '../lib/ui'

// One shared up/down status pill used by BOTH the Devices and Servers lists —
// green "Up" / red "Down", no duration inside (the "Last Change" column carries
// the time). Replaces the old Online/Offline (servers) and Active/Unreachable
// (devices) wording so both pages read identically.
export default function StatusBadge({ up }: { up: boolean }) {
  return <span className={badge(up ? 'ok' : 'down')}>{up ? 'Up' : 'Down'}</span>
}
