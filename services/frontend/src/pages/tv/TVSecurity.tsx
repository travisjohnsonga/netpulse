import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVStat, TVPanel } from './TVLayout'
import { api } from '../../api/client'

interface AuditRow {
  id: number
  created_at: string
  event_type: string
  username: string
  ip_address: string | null
  target_name: string
  success: boolean
  description: string
}

// Recent security-relevant audit events (failed/auth/admin). The audit-log
// endpoint is paginated; tolerate both array and {results} shapes.
async function fetchSecurityEvents(): Promise<AuditRow[]> {
  const { data } = await api.get('/audit-log/', { params: { page_size: 50 } })
  const rows: AuditRow[] = Array.isArray(data) ? data : (data.results ?? [])
  return rows
}

export default function TVSecurity({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data } = useQuery({ queryKey: ['tv-security'], queryFn: fetchSecurityEvents, refetchInterval: 30_000, placeholderData: (p) => p })
  const rows = data ?? []
  const failures = rows.filter((r) => !r.success)
  const sources = new Set(failures.map((r) => r.ip_address).filter(Boolean))
  const users = new Set(failures.map((r) => r.username).filter(Boolean))

  return (
    <TVLayout title="Security Events" refreshInterval={30} rotation={rotation}>
      <div className="flex h-full flex-col gap-6">
        <div className="grid grid-cols-3 gap-6">
          <TVStat label="Failures (recent)" value={failures.length} color={failures.length > 0 ? TV.error : TV.success} />
          <TVStat label="Source IPs" value={sources.size} />
          <TVStat label="Users" value={users.size} />
        </div>
        <TVPanel title="Recent Events" className="flex-1 overflow-auto">
          <div className="space-y-1 font-mono text-lg">
            {rows.slice(0, 18).map((r) => (
              <div key={r.id} className="flex gap-4">
                <span style={{ color: TV.muted }}>{new Date(r.created_at).toLocaleTimeString()}</span>
                <span style={{ color: r.success ? TV.success : TV.error, width: 56 }}>{r.success ? 'OK' : 'FAIL'}</span>
                <span style={{ color: TV.accent, width: 220 }} className="truncate">{r.event_type}</span>
                <span className="truncate">{r.username || r.ip_address || r.target_name}</span>
              </div>
            ))}
            {rows.length === 0 && <div style={{ color: TV.muted }}>No recent audit events.</div>}
          </div>
        </TVPanel>
      </div>
    </TVLayout>
  )
}
