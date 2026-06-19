import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVPanel } from './TVLayout'
import { fetchDevices } from '../../api/client'

export default function TVSites({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data } = useQuery({ queryKey: ['tv-sites-devices'], queryFn: () => fetchDevices(), refetchInterval: 45_000, placeholderData: (p) => p })
  const list = data?.results ?? []

  const sites = new Map<string, { up: number; total: number; down: typeof list }>()
  for (const d of list) {
    const name = d.site_name || 'Unassigned'
    const e = sites.get(name) || { up: 0, total: 0, down: [] }
    e.total++
    if (d.status === 'unreachable') e.down.push(d)
    else e.up++
    sites.set(name, e)
  }
  const ordered = [...sites.entries()].sort((a, b) => b[1].down.length - a[1].down.length)

  return (
    <TVLayout title="Site Status" refreshInterval={45} rotation={rotation}>
      <div className="grid h-full grid-cols-2 gap-6 overflow-auto">
        {ordered.map(([name, e]) => {
          const pct = e.total ? Math.round((e.up / e.total) * 100) : 100
          return (
            <TVPanel key={name}>
              <div className="flex items-baseline justify-between">
                <span className="text-2xl font-semibold">{name}</span>
                <span className="text-xl" style={{ color: e.down.length ? TV.warning : TV.success }}>{e.up}/{e.total} online</span>
              </div>
              <div className="mt-3 h-3 w-full rounded-full" style={{ background: TV.bg }}>
                <div className="h-3 rounded-full" style={{ width: `${pct}%`, background: e.down.length ? TV.warning : TV.success }} />
              </div>
              <div className="mt-3 space-y-1 text-lg">
                {e.down.slice(0, 5).map((d) => (
                  <div key={d.id} style={{ color: TV.error }}>🔴 {d.display_hostname || d.hostname}</div>
                ))}
                {e.down.length === 0 && <div style={{ color: TV.success }}>All devices online</div>}
              </div>
            </TVPanel>
          )
        })}
        {ordered.length === 0 && <div style={{ color: TV.muted }} className="text-2xl">No devices.</div>}
      </div>
    </TVLayout>
  )
}
