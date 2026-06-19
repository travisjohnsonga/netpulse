import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { TV } from './TVLayout'

/**
 * /tv — TV Dashboard launcher. Large tiles for a NOC monitor, plus an
 * auto-rotation builder that hands off to /tv/rotate. No app chrome.
 */

export interface TVScreen {
  key: string
  path: string
  icon: string
  title: string
  blurb: string
  interval: number
}

export const TV_SCREENS: TVScreen[] = [
  { key: 'network', path: '/tv/network', icon: '📡', title: 'Network Overview', blurb: 'Devices, alerts, status', interval: 45 },
  { key: 'wireless', path: '/tv/wireless-mist', icon: '📶', title: 'Wireless (Mist)', blurb: 'APs, SLE, floor map, clients', interval: 30 },
  { key: 'security', path: '/tv/security', icon: '🔒', title: 'Security Events', blurb: 'Auth failures, alerts, threats', interval: 30 },
  { key: 'ops', path: '/tv/ops', icon: '📊', title: 'Operations Status', blurb: 'Collection, agents, services', interval: 60 },
  { key: 'sites', path: '/tv/sites', icon: '📍', title: 'Site Status', blurb: 'Per-site device up/down', interval: 45 },
  { key: 'servers', path: '/tv/servers', icon: '🖥️', title: 'Server Health', blurb: 'CPU, mem, disk across servers', interval: 30 },
  { key: 'compliance', path: '/tv/compliance', icon: '✅', title: 'Compliance Status', blurb: 'Framework coverage, gaps', interval: 300 },
]

export default function TVLauncher() {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<Record<string, boolean>>(
    () => Object.fromEntries(TV_SCREENS.map((s) => [s.key, ['network', 'wireless', 'security'].includes(s.key)])),
  )
  const [interval, setIntervalSecs] = useState(30)

  const startRotation = () => {
    const screens = TV_SCREENS.filter((s) => selected[s.key]).map((s) => s.key)
    if (screens.length === 0) return
    navigate(`/tv/rotate?screens=${screens.join(',')}&interval=${interval}`)
  }

  return (
    <div className="fixed inset-0 z-50 overflow-auto p-10" style={{ background: TV.bg, color: TV.text }}>
      <div className="mx-auto max-w-6xl">
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-5xl font-bold" style={{ color: TV.accent }}>spane</h1>
            <p className="mt-1 text-2xl" style={{ color: TV.muted }}>TV Dashboard Mode</p>
          </div>
          <Link to="/dashboard" className="rounded-lg px-4 py-2 text-lg" style={{ background: TV.card }}>← Back to app</Link>
        </div>

        <div className="mt-10 grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {TV_SCREENS.map((s) => (
            <Link key={s.key} to={s.path} className="group rounded-2xl p-7 transition-transform hover:scale-[1.02]"
              style={{ background: TV.card }}>
              <div className="text-5xl">{s.icon}</div>
              <div className="mt-4 text-2xl font-semibold">{s.title}</div>
              <div className="mt-1 text-lg" style={{ color: TV.muted }}>{s.blurb}</div>
            </Link>
          ))}
        </div>

        <div className="mt-10 rounded-2xl p-7" style={{ background: TV.card }}>
          <div className="text-2xl font-semibold">Auto-Rotate Dashboards</div>
          <p className="mt-1 text-lg" style={{ color: TV.muted }}>Cycle through selected dashboards on a dedicated screen.</p>
          <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {TV_SCREENS.map((s) => (
              <label key={s.key} className="flex items-center gap-3 text-xl">
                <input type="checkbox" className="h-5 w-5" checked={!!selected[s.key]}
                  onChange={(e) => setSelected((p) => ({ ...p, [s.key]: e.target.checked }))} />
                {s.icon} {s.title}
              </label>
            ))}
          </div>
          <div className="mt-6 flex items-center gap-4">
            <label className="text-xl">Interval
              <select value={interval} onChange={(e) => setIntervalSecs(Number(e.target.value))}
                className="ml-3 rounded-lg px-3 py-2 text-lg" style={{ background: TV.bg, color: TV.text, border: `1px solid ${TV.muted}` }}>
                {[15, 30, 45, 60, 120].map((n) => <option key={n} value={n}>{n}s</option>)}
              </select>
            </label>
            <button onClick={startRotation} className="rounded-lg px-6 py-3 text-xl font-semibold"
              style={{ background: TV.accent, color: '#fff' }}>Start Rotation</button>
          </div>
        </div>
      </div>
    </div>
  )
}
