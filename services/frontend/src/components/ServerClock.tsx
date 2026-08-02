import { useEffect, useRef, useState } from 'react'
import { checkHealth } from '../api/client'
import { usePreferencesStore } from '../store/preferencesStore'

// Footer clock: live-ticking SERVER time, shown in both UTC and the user's
// timezone — schedules run in UTC but operators think in local, so this lets you
// reconcile "my 19:00 schedule = 01:00 UTC" at a glance. If the user's tz is UTC
// (the default), only UTC is shown.
//
// Anchored to SERVER time, not the browser clock: fetch /health once on mount
// (server_time = the backend's UTC now), compute the browser↔server offset ONCE,
// then tick locally every second from that anchor. So it shows true server time
// even when the browser clock is wrong, without polling every second.
//
// The user-tz conversion mirrors apps.reports.schedule_tz (backend, Python); the
// browser equivalent of that IANA conversion is Intl.DateTimeFormat({ timeZone }).
export default function ServerClock() {
  const userTz = usePreferencesStore((s) => s.prefs?.timezone) || 'UTC'
  const offsetRef = useRef(0) // serverMs − browserMs, computed once from /health
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let cancelled = false
    checkHealth()
      .then((h) => {
        if (cancelled || !h.server_time) return
        const serverMs = Date.parse(h.server_time)
        if (!Number.isNaN(serverMs)) offsetRef.current = serverMs - Date.now()
      })
      .catch(() => {}) // fall back to the browser clock (offset 0)
    const t = setInterval(() => setNow(new Date(Date.now() + offsetRef.current)), 1000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  const utc = fmtZone(now, 'UTC')
  const isUtcUser = !userTz || userTz === 'UTC' || userTz === 'Etc/UTC'
  const local = isUtcUser ? null : fmtZone(now, userTz)

  return (
    <span className="text-xs text-gray-500 tabular-nums" title="Current server time">
      🕓 {utc.time} {utc.abbr}
      {local && <> · {local.time} {local.abbr}</>}
    </span>
  )
}

// HH:MM in the given IANA zone plus its short abbreviation (e.g. "19:32", "CDT").
function fmtZone(d: Date, timeZone: string): { time: string; abbr: string } {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZoneName: 'short',
    }).formatToParts(d)
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? ''
    return { time: `${get('hour')}:${get('minute')}`, abbr: get('timeZoneName') }
  } catch {
    // Invalid/unknown tz → fall back to UTC so the clock never breaks.
    return timeZone === 'UTC' ? { time: '--:--', abbr: 'UTC' } : fmtZone(d, 'UTC')
  }
}
