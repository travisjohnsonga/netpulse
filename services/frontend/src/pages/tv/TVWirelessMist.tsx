import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'

/**
 * /tv/wireless-mist — warehouse Wireless TV dashboard (Juniper Mist). 16:9,
 * glanceable from across a floor: floor-plan map with AP circles (sized by
 * client count, coloured by reachability) + located client dots on the left;
 * big-number tiles, SLE ring gauges, and busiest-APs on the right.
 *
 * Reuses the server-assembled GET /api/wireless/location/ payload (the Mist
 * token never reaches the browser — see apps/integrations/mist_location.py).
 * `?site=<mist-site-id>` selects the site; defaults to the first synced site.
 */

const C = {
  bg: '#060810', card: '#0d1117', border: '#1f2937', text: '#f0f4f8', muted: '#6b7280',
  green: '#10b981', yellow: '#f59e0b', orange: '#f97316', red: '#ef4444', blue: '#3b82f6', purple: '#a855f7',
}
const REFRESH_MS = 30_000

interface MapMeta { id: string; name: string; image_url: string; width: number; height: number; ppm: number }
interface ApMarker { name: string; mac: string; x: number; y: number; status: string; clients: number | null }
interface ClientMarker { mac: string; name: string; x: number; y: number; band: string; rssi: number | null }
interface Summary { clients_online: number; clients_total: number; aps_online: number; aps_total: number; throughput_mbps: number }
interface Sle { roaming: number | null; coverage: number | null; time_to_connect: number | null; throughput: number | null }
interface LocationPayload { map: MapMeta; aps: ApMarker[]; clients: ClientMarker[]; summary: Summary; sle: Sle }

interface MistSiteOption { mist_id: string; name: string }

async function fetchMistSites(): Promise<MistSiteOption[]> {
  const { data } = await api.get<MistSiteOption[]>('/integrations/mist/sites/')
  return data
}
async function fetchLocation(siteId: string): Promise<LocationPayload> {
  const { data } = await api.get<LocationPayload>('/wireless/location/', { params: { site: siteId } })
  return data
}

const apRadius = (clients: number) => (clients >= 30 ? 36 : clients >= 16 ? 32 : clients >= 6 ? 28 : clients >= 1 ? 24 : 20) / 2
function sleColor(v: number | null): string { if (v == null) return C.muted; if (v >= 90) return C.green; if (v >= 75) return C.yellow; return C.red }

function SleRing({ label, value }: { label: string; value: number | null }) {
  const pct = value ?? 0
  const color = sleColor(value)
  return (
    <div className="flex flex-col items-center justify-center">
      <div className="relative h-24 w-24 rounded-full" style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, ${C.border} 0deg)` }}>
        <div className="absolute inset-[7px] flex items-center justify-center rounded-full" style={{ background: C.card }}>
          <span className="text-xl font-bold tabular-nums" style={{ color: C.text }}>{value == null ? '—' : `${Math.round(value)}%`}</span>
        </div>
      </div>
      <span className="mt-1 text-sm uppercase tracking-wide" style={{ color: C.muted }}>{label}</span>
    </div>
  )
}

function Tile({ icon, label, value, sub, color }: { icon: string; label: string; value: string; sub?: string; color: string }) {
  return (
    <div className="flex flex-col justify-center rounded-xl px-5 py-3" style={{ background: C.card, border: `1px solid ${C.border}` }}>
      <div className="text-sm uppercase tracking-widest" style={{ color: C.muted }}>{icon} {label}</div>
      <div className="font-bold tabular-nums leading-none" style={{ fontSize: 64, color }}>{value}</div>
      {sub && <div className="text-base" style={{ color: C.muted }}>{sub}</div>}
    </div>
  )
}

function FloorMap({ data }: { data: LocationPayload }) {
  const { map, aps, clients } = data
  const toPct = (v: number, span: number) => (span > 0 ? (v / span) * 100 : 0)
  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl" style={{ background: '#04060c', border: `1px solid ${C.border}` }}>
      {map.image_url ? (
        <img src={map.image_url} alt={map.name} className="absolute inset-0 h-full w-full object-contain opacity-60" />
      ) : (
        <div className="absolute inset-0" style={{ backgroundImage: `linear-gradient(${C.border} 1px, transparent 1px), linear-gradient(90deg, ${C.border} 1px, transparent 1px)`, backgroundSize: '48px 48px', opacity: 0.4 }} />
      )}
      {clients.map((c) => (
        <span key={c.mac} className="absolute block h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full transition-all duration-700"
          style={{ left: `${toPct(c.x, map.width)}%`, top: `${toPct(c.y, map.height)}%`, background: C.blue, boxShadow: '0 0 6px rgba(59,130,246,0.8)' }} />
      ))}
      {aps.map((ap) => {
        const online = ap.status === 'connected'
        const r = apRadius(ap.clients ?? 0)
        const color = online ? C.green : '#555a66'
        return (
          <div key={ap.mac} className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${toPct(ap.x, map.width)}%`, top: `${toPct(ap.y, map.height)}%` }}
            title={`${ap.name} · ${online ? 'online' : 'offline'} · ${ap.clients ?? 0} clients`}>
            <div className="relative rounded-full" style={{ width: r * 2, height: r * 2, background: `${color}40`, border: `2px solid ${color}` }}>
              {(ap.clients ?? 0) > 0 && (
                <span className="absolute -right-2 -top-2 rounded-full px-1.5 text-xs font-bold" style={{ background: C.blue, color: '#fff' }}>{ap.clients}</span>
              )}
            </div>
            <div className="mt-0.5 max-w-[90px] truncate text-center text-[11px]" style={{ color: C.muted }}>{ap.name.replace(/^.*-(ap-?\d+)$/i, '$1')}</div>
          </div>
        )
      })}
    </div>
  )
}

export default function TVWirelessMist() {
  const params = new URLSearchParams(window.location.search)
  const [siteId, setSiteId] = useState<string | undefined>(params.get('site') || undefined)
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000)
  const [now, setNow] = useState(() => new Date().toLocaleTimeString())

  const { data: sites } = useQuery({ queryKey: ['mist-sites'], queryFn: fetchMistSites })
  useEffect(() => { if (!siteId && sites && sites.length > 0) setSiteId(sites[0].mist_id) }, [sites, siteId])

  const { data } = useQuery({
    queryKey: ['tv-wireless-mist', siteId],
    queryFn: () => fetchLocation(siteId!),
    enabled: !!siteId,
    refetchInterval: REFRESH_MS,
    placeholderData: (p) => p,
  })

  useEffect(() => {
    const t = setInterval(() => { setCountdown((c) => (c <= 1 ? REFRESH_MS / 1000 : c - 1)); setNow(new Date().toLocaleTimeString()) }, 1000)
    return () => clearInterval(t)
  }, [])

  const topAps = useMemo(() => [...(data?.aps ?? [])].sort((a, b) => (b.clients ?? 0) - (a.clients ?? 0)).slice(0, 3), [data])
  const maxClients = Math.max(1, ...(topAps.map((a) => a.clients ?? 0)))
  const siteName = sites?.find((s) => s.mist_id === siteId)?.name || data?.map.name || 'Wireless'

  if (sites && sites.length === 0)
    return <div className="fixed inset-0 z-50 grid place-items-center" style={{ background: C.bg, color: C.muted }}>No Mist sites. Connect a Mist account in Settings → Integrations.</div>

  const s = data?.summary
  const sle = data?.sle
  const handheldsOk = s ? s.clients_total === 0 || s.clients_online / Math.max(1, s.clients_total) > 0.9 : true
  const apsOk = s ? s.aps_online === s.aps_total : true

  return (
    <div className="fixed inset-0 z-50 flex flex-col" style={{ background: C.bg, color: C.text, fontFamily: 'system-ui, Inter, sans-serif' }}>
      <header className="flex items-center justify-between px-6 py-2" style={{ borderBottom: `1px solid ${C.border}` }}>
        <div className="text-xl font-semibold"><span style={{ color: C.blue }}>spane</span> · {siteName} · Wireless</div>
        <div className="flex items-center gap-4 text-base tabular-nums" style={{ color: C.muted }}>↻ {countdown}s | {now}</div>
      </header>

      {!data ? (
        <div className="flex flex-1 items-center justify-center text-2xl" style={{ color: C.muted }}>Loading Mist wireless data…</div>
      ) : (
        <div className="grid flex-1 grid-cols-[3fr_2fr] gap-4 overflow-hidden p-4">
          <FloorMap data={data} />
          <div className="grid grid-rows-[auto_auto_1fr] gap-4 overflow-hidden">
            <div className="grid grid-cols-3 gap-3">
              <Tile icon="📱" label="Clients" value={String(s?.clients_online ?? 0)} sub={`of ${s?.clients_total ?? 0}`} color={handheldsOk ? C.green : C.yellow} />
              <Tile icon="📡" label="APs Up" value={`${s?.aps_online ?? 0}/${s?.aps_total ?? 0}`} sub={apsOk ? 'all up' : `${(s?.aps_total ?? 0) - (s?.aps_online ?? 0)} offline ⚠️`} color={apsOk ? C.green : C.orange} />
              <Tile icon="📶" label="Throughput" value={`${s?.throughput_mbps ?? 0}`} sub="Mbps" color={C.text} />
            </div>
            <div className="grid grid-cols-2 grid-rows-2 gap-2 rounded-xl p-3" style={{ background: C.card, border: `1px solid ${C.border}` }}>
              <SleRing label="Roaming" value={sle?.roaming ?? null} />
              <SleRing label="Coverage" value={sle?.coverage ?? null} />
              <SleRing label="Throughput" value={sle?.throughput ?? null} />
              <SleRing label="Connect" value={sle?.time_to_connect ?? null} />
            </div>
            <div className="overflow-auto rounded-xl p-4" style={{ background: C.card, border: `1px solid ${C.border}` }}>
              <div className="mb-3 text-sm uppercase tracking-widest" style={{ color: C.muted }}>Busiest APs</div>
              <div className="space-y-3">
                {topAps.map((ap, i) => (
                  <div key={ap.mac} className="flex items-center gap-3">
                    <span className="w-5 text-right text-lg" style={{ color: C.muted }}>{i + 1}.</span>
                    <span className="w-44 truncate text-lg">{ap.name}</span>
                    <div className="h-4 flex-1 rounded" style={{ background: C.bg }}>
                      <div className="h-4 rounded" style={{ width: `${((ap.clients ?? 0) / maxClients) * 100}%`, background: ap.status === 'connected' ? C.green : C.muted }} />
                    </div>
                    <span className="w-24 text-right tabular-nums text-lg">{ap.clients ?? 0} cl.</span>
                  </div>
                ))}
                {topAps.length === 0 && <div style={{ color: C.muted }}>No access points on this map.</div>}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
