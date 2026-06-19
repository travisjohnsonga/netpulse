import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVPanel } from './TVLayout'
import { fetchServers } from '../../api/client'

function pctColor(v: number | null): string {
  if (v == null) return TV.muted
  if (v >= 90) return TV.error
  if (v >= 75) return TV.warning
  return TV.success
}
const fmt = (v: number | null) => (v == null ? '—' : `${Math.round(v)}%`)

export default function TVServers({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data } = useQuery({ queryKey: ['tv-servers'], queryFn: fetchServers, refetchInterval: 30_000, placeholderData: (p) => p })
  const servers = data ?? []

  return (
    <TVLayout title="Server Health" refreshInterval={30} rotation={rotation}>
      <TVPanel className="h-full overflow-auto">
        <table className="w-full text-xl">
          <thead>
            <tr style={{ color: TV.muted }} className="text-left uppercase tracking-widest">
              <th className="py-3">Server</th><th>CPU</th><th>Mem</th><th>Disk</th><th>Status</th>
            </tr>
          </thead>
          <tbody>
            {servers.map((s) => {
              const m = s.latest_metrics
              const ok = s.status === 'active'
              return (
                <tr key={s.id} style={{ borderTop: `1px solid ${TV.bg}` }}>
                  <td className="py-3 font-semibold">{s.hostname}</td>
                  <td style={{ color: pctColor(m?.cpu_pct ?? null) }}>{fmt(m?.cpu_pct ?? null)}</td>
                  <td style={{ color: pctColor(m?.memory_pct ?? null) }}>{fmt(m?.memory_pct ?? null)}</td>
                  <td style={{ color: pctColor(m?.disk_max_pct ?? null) }}>{fmt(m?.disk_max_pct ?? null)}</td>
                  <td style={{ color: ok ? TV.success : TV.error }}>{ok ? '✅' : '🔴'}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {servers.length === 0 && <div style={{ color: TV.muted }} className="mt-4 text-xl">No servers (agents) enrolled.</div>}
      </TVPanel>
    </TVLayout>
  )
}
