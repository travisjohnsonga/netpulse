import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'
import { api } from '../api/client'

/**
 * Warehouse WiFi-client location dashboard (Mist). Built for an always-on TV:
 * dark, large type, glanceable, auto-refreshing. Append `?kiosk=1` to the URL
 * to drop the app chrome and go full-bleed (point a kiosk browser here).
 *
 * Data: GET /api/wireless/location/?site=&map= (assembled server-side from Mist;
 * the token stays in the api container). WiFi clients only.
 *
 * Coordinates: the API returns marker x/y and the map width/height all in the
 * floor-plan image's PIXELS, so a marker sits at `x / width` of the image — no
 * pixels-per-metre scaling on the client.
 *
 * `site` is a *Mist* site id (UUID), not the global NetPulse site filter, so the
 * page has its own Mist-site selector fed by /api/integrations/mist/sites/.
 */
const REFRESH_MS = 5000

// ── Types (mirror mist_location.build_payload) ───────────────────────────────
interface MapMeta { id: string; name: string; image_url: string; width: number; height: number; ppm: number }
interface ApMarker { name: string; mac: string; x: number; y: number; status: string; clients: number | null }
interface ClientMarker { name: string; mac: string; x: number; y: number; band: string; rssi: number | null; ap_mac: string; num_locating_aps: number | null; last_seen: number | null }
interface Summary { clients_online: number; clients_total: number; aps_online: number; aps_total: number; throughput_mbps: number }
interface Sle { roaming: number | null; coverage: number | null; time_to_connect: number | null; throughput: number | null }
interface LocationPayload { map: MapMeta; aps: ApMarker[]; clients: ClientMarker[]; summary: Summary; sle: Sle; generated: number }

interface MistSiteOption { mist_id: string; name: string }

async function fetchMistSites(): Promise<MistSiteOption[]> {
  const { data } = await api.get<MistSiteOption[]>('/integrations/mist/sites/')
  return data
}

async function fetchLocation(siteId: string, mapId?: string): Promise<LocationPayload> {
  const { data } = await api.get<LocationPayload>('/wireless/location/', {
    params: { site: siteId, ...(mapId ? { map: mapId } : {}) },
  })
  return data
}

// ── Visual helpers ───────────────────────────────────────────────────────────
const BAND_COLOR: Record<string, string> = { '24': '#f59e0b', '5': '#3b82f6', '6': '#22c55e' }
const bandColor = (b: string) => BAND_COLOR[b] || '#94a3b8'

function sleColor(v: number | null): string {
  if (v == null) return '#6b7280'
  if (v >= 90) return '#22c55e'
  if (v >= 75) return '#eab308'
  return '#ef4444'
}

/** SLE percentage as a ring gauge. */
function SleRing({ label, value }: { label: string; value: number | null }) {
  const pct = value ?? 0
  const color = sleColor(value)
  return (
    <div className="flex flex-col items-center gap-1">
      <div
        className="relative h-20 w-20 rounded-full"
        style={{ background: `conic-gradient(${color} ${pct * 3.6}deg, #1f2937 0deg)` }}
      >
        <div className="absolute inset-[6px] flex items-center justify-center rounded-full bg-gray-900">
          <span className="text-lg font-bold text-white">{value == null ? '—' : `${value}%`}</span>
        </div>
      </div>
      <span className="text-xs uppercase tracking-wide text-gray-400">{label}</span>
    </div>
  )
}

/** A big-number tile. */
function Tile({ label, value, sub, color = 'text-white' }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div className="rounded-xl bg-gray-800/80 px-5 py-4">
      <div className="text-xs uppercase tracking-wider text-gray-400">{label}</div>
      <div className={clsx('mt-1 text-4xl font-bold tabular-nums', color)}>{value}</div>
      {sub && <div className="mt-0.5 text-sm text-gray-500">{sub}</div>}
    </div>
  )
}

/** The floor plan with AP + client markers, positioned by pixel x/y as a % of
 *  the floor-plan image's pixel dimensions. */
function FloorMap({ data }: { data: LocationPayload }) {
  const { map, aps, clients } = data
  const toPct = (v: number, span: number) => (span > 0 ? (v / span) * 100 : 0)
  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl bg-gray-950">
      {map.image_url ? (
        <img src={map.image_url} alt={map.name} className="absolute inset-0 h-full w-full object-contain opacity-70" />
      ) : (
        <div className="absolute inset-0 grid place-items-center text-gray-600">no floor-plan image</div>
      )}
      {/* AP placements (dimmed when disconnected) */}
      {aps.map((ap) => (
        <div
          key={ap.mac}
          className="absolute -translate-x-1/2 -translate-y-1/2"
          style={{ left: `${toPct(ap.x, map.width)}%`, top: `${toPct(ap.y, map.height)}%` }}
          title={`${ap.name}${ap.status ? ` · ${ap.status}` : ''}`}
        >
          <div className={clsx('h-3 w-3 rotate-45 border', ap.status === 'connected'
            ? 'border-cyan-300 bg-cyan-500/30' : 'border-gray-500 bg-gray-600/30')} />
        </div>
      ))}
      {/* Located WiFi clients */}
      {clients.map((c) => (
        <div
          key={c.mac}
          className="absolute -translate-x-1/2 -translate-y-1/2 transition-all duration-700 ease-out"
          style={{ left: `${toPct(c.x, map.width)}%`, top: `${toPct(c.y, map.height)}%` }}
          title={`${c.name} · ${c.rssi ?? '?'} dBm`}
        >
          <span
            className="block h-3.5 w-3.5 rounded-full ring-2 ring-white/80 shadow-lg"
            style={{ backgroundColor: bandColor(c.band) }}
          />
        </div>
      ))}
    </div>
  )
}

export default function WirelessLocation() {
  const kiosk = new URLSearchParams(window.location.search).get('kiosk') === '1'
  const [siteId, setSiteId] = useState<string | undefined>(undefined)
  const [mapId] = useState<string | undefined>(undefined)

  // Mist sites for the selector (these are Mist UUIDs, not NetPulse site ids).
  const { data: sites } = useQuery({ queryKey: ['mist-sites'], queryFn: fetchMistSites })
  useEffect(() => {
    if (!siteId && sites && sites.length > 0) setSiteId(sites[0].mist_id)
  }, [sites, siteId])

  const { data, isLoading, error } = useQuery({
    queryKey: ['wireless-location', siteId, mapId],
    queryFn: () => fetchLocation(siteId!, mapId),
    enabled: !!siteId,
    refetchInterval: REFRESH_MS,
    placeholderData: (prev) => prev, // keep markers on screen between polls
  })

  const updated = useMemo(
    () => (data ? new Date(data.generated * 1000).toLocaleTimeString() : '—'),
    [data],
  )

  if (sites && sites.length === 0)
    return <div className="p-8 text-gray-400">No Mist sites found. Connect a Mist account and sync in Settings → Integrations.</div>
  if (!siteId) return <div className="p-8 text-gray-400">Loading Mist sites…</div>
  if (isLoading && !data) return <div className="p-8 text-gray-400">Loading location data…</div>
  if (error && !data)
    return <div className="p-8 text-red-400">Could not load Mist location data.</div>
  if (!data) return null

  const { summary, sle } = data
  return (
    <div className={clsx('flex flex-col bg-gray-900 text-white', kiosk ? 'fixed inset-0 z-50 p-6' : 'gap-4 p-4 min-h-[80vh]')}>
      <div className="flex items-baseline justify-between gap-4">
        <h1 className="text-2xl font-bold">{data.map.name} · Live Coverage</h1>
        <div className="flex items-center gap-3">
          {!kiosk && sites && sites.length > 1 && (
            <select
              value={siteId}
              onChange={(e) => setSiteId(e.target.value)}
              className="rounded-md border border-gray-700 bg-gray-800 px-2 py-1 text-sm text-gray-200"
            >
              {sites.map((s) => <option key={s.mist_id} value={s.mist_id}>{s.name}</option>)}
            </select>
          )}
          <span className="text-sm text-gray-500">updated {updated}</span>
        </div>
      </div>
      <div className="grid flex-1 grid-cols-1 gap-4 lg:grid-cols-[3fr_1fr]">
        {/* Map */}
        <div className="min-h-[50vh]">
          <FloorMap data={data} />
        </div>
        {/* Right rail: tiles + SLE + legend */}
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3">
            <Tile label="Clients" value={String(summary.clients_online)} sub={`${summary.clients_total} total`} color="text-blue-400" />
            <Tile
              label="APs Up"
              value={`${summary.aps_online}/${summary.aps_total}`}
              color={summary.aps_online === summary.aps_total ? 'text-green-400' : 'text-yellow-400'}
            />
            <Tile label="Throughput" value={`${summary.throughput_mbps}`} sub="Mbps" color="text-cyan-400" />
            <Tile label="Located" value={`${summary.clients_online}`} sub="on map" color="text-white" />
          </div>
          <div className="rounded-xl bg-gray-800/80 p-4">
            <div className="mb-3 text-xs uppercase tracking-wider text-gray-400">Service Levels</div>
            <div className="grid grid-cols-2 gap-4">
              <SleRing label="Roaming" value={sle.roaming} />
              <SleRing label="Coverage" value={sle.coverage} />
              <SleRing label="Connect" value={sle.time_to_connect} />
              <SleRing label="Throughput" value={sle.throughput} />
            </div>
          </div>
          <div className="rounded-xl bg-gray-800/80 p-4 text-sm">
            <div className="mb-2 text-xs uppercase tracking-wider text-gray-400">Legend</div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-gray-300">
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full" style={{ background: BAND_COLOR['5'] }} />5 GHz</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full" style={{ background: BAND_COLOR['24'] }} />2.4 GHz</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rounded-full" style={{ background: BAND_COLOR['6'] }} />6 GHz</span>
              <span className="flex items-center gap-1.5"><span className="h-3 w-3 rotate-45 border border-cyan-300 bg-cyan-500/30" />AP</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
