import { useEffect, useMemo, useState, type ComponentType } from 'react'
import { useSearchParams } from 'react-router-dom'
import { TV_SCREENS } from './TVLauncher'
import TVNetwork from './TVNetwork'
import TVWirelessMist from './TVWirelessMist'
import TVSecurity from './TVSecurity'
import TVOps from './TVOps'
import TVSites from './TVSites'
import TVServers from './TVServers'
import TVCompliance from './TVCompliance'
import { TV } from './TVLayout'

type RotationProp = { rotation?: { current: string; nextCountdown: number; progressPct: number } }

const SCREENS: Record<string, ComponentType<RotationProp>> = {
  network: TVNetwork,
  wireless: TVWirelessMist,
  security: TVSecurity,
  ops: TVOps,
  sites: TVSites,
  servers: TVServers,
  compliance: TVCompliance,
}

/**
 * /tv/rotate?screens=network,wireless,security&interval=30 — cycles through the
 * selected dashboards on a timer, each showing the next-screen countdown and a
 * progress bar (passed down via the rotation prop to TVLayout).
 */
export default function TVRotate() {
  const [params] = useSearchParams()
  const interval = Math.max(5, Number(params.get('interval')) || 30)
  const keys = useMemo(
    () => (params.get('screens') || 'network')
      .split(',').map((k) => k.trim()).filter((k) => SCREENS[k]),
    [params],
  )

  const [idx, setIdx] = useState(0)
  const [remaining, setRemaining] = useState(interval)

  useEffect(() => {
    setIdx(0)
    setRemaining(interval)
  }, [interval, keys.length])

  useEffect(() => {
    const t = setInterval(() => {
      setRemaining((r) => {
        if (r <= 1) {
          setIdx((i) => (i + 1) % Math.max(1, keys.length))
          return interval
        }
        return r - 1
      })
    }, 1000)
    return () => clearInterval(t)
  }, [interval, keys.length])

  if (keys.length === 0) {
    return <div className="fixed inset-0 z-50 grid place-items-center" style={{ background: TV.bg, color: TV.text }}>No dashboards selected.</div>
  }

  const activeKey = keys[idx % keys.length]
  const nextKey = keys[(idx + 1) % keys.length]
  const Active = SCREENS[activeKey]
  const nextTitle = TV_SCREENS.find((s) => s.key === nextKey)?.title || nextKey

  return (
    <Active rotation={{
      current: nextTitle,
      nextCountdown: remaining,
      progressPct: ((interval - remaining) / interval) * 100,
    }} />
  )
}
