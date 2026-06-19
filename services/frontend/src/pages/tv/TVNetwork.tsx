import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVStat, TVPanel } from './TVLayout'
import { fetchDevices, fetchAlerts } from '../../api/client'

const SEV_COLOR: Record<string, string> = { critical: TV.error, high: '#e67e22', medium: TV.warning, low: TV.accent, info: TV.muted }

export default function TVNetwork({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data: devs } = useQuery({ queryKey: ['tv-devices'], queryFn: () => fetchDevices(), refetchInterval: 45_000, placeholderData: (p) => p })
  const { data: alerts } = useQuery({ queryKey: ['tv-alerts'], queryFn: () => fetchAlerts('false'), refetchInterval: 45_000, placeholderData: (p) => p })

  const list = devs?.results ?? []
  const total = devs?.count ?? list.length
  const down = list.filter((d) => d.status === 'unreachable').length
  const online = (total || list.length) - down
  const active = alerts ?? []
  const bySev = active.reduce<Record<string, number>>((a, x) => { const s = x.effective_severity || x.severity; a[s] = (a[s] || 0) + 1; return a }, {})

  // Per-site online/total
  const sites = new Map<string, { up: number; total: number }>()
  for (const d of list) {
    const s = d.site_name || 'Unassigned'
    const e = sites.get(s) || { up: 0, total: 0 }
    e.total++; if (d.status !== 'unreachable') e.up++
    sites.set(s, e)
  }

  return (
    <TVLayout title="Network Overview" refreshInterval={45} rotation={rotation}>
      <div className="grid h-full grid-cols-[1fr_1.3fr] gap-6">
        <div className="flex flex-col gap-6">
          <div className="grid grid-cols-2 gap-6">
            <TVStat label="Devices" value={total} />
            <TVStat label="Online" value={online} color={TV.success} />
            <TVStat label="Down" value={down} color={down > 0 ? TV.error : TV.success} />
            <TVStat label="Active Alerts" value={active.length} color={active.length > 0 ? TV.warning : TV.success} />
          </div>
          <TVPanel title="Sites" className="flex-1 overflow-auto">
            <div className="space-y-2 text-xl">
              {[...sites.entries()].map(([name, e]) => (
                <div key={name} className="flex justify-between">
                  <span>{name}</span>
                  <span style={{ color: e.up === e.total ? TV.success : TV.warning }}>{e.up}/{e.total}</span>
                </div>
              ))}
            </div>
          </TVPanel>
        </div>

        <TVPanel title="Active Alerts" className="overflow-auto">
          {active.length === 0 ? (
            <div className="text-2xl" style={{ color: TV.success }}>✅ No active alerts</div>
          ) : (
            <div className="space-y-3">
              {(['critical', 'high', 'medium', 'low'] as const).map((sev) => bySev[sev] ? (
                <div key={sev}>
                  <div className="text-xl font-semibold uppercase" style={{ color: SEV_COLOR[sev] }}>{sev} ({bySev[sev]})</div>
                  {active.filter((a) => (a.effective_severity || a.severity) === sev).slice(0, 6).map((a) => (
                    <div key={a.id} className="ml-2 text-lg" style={{ color: TV.text }}>
                      • {a.device || a.title} — {a.title}
                    </div>
                  ))}
                </div>
              ) : null)}
            </div>
          )}
        </TVPanel>
      </div>
    </TVLayout>
  )
}
