import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVStat, TVPanel } from './TVLayout'
import { fetchCollectionHealth, fetchAgents, fetchAlerts, api } from '../../api/client'

interface CheckSummary { total: number; up: number; down: number; degraded: number }
async function fetchCheckSummary(): Promise<CheckSummary | null> {
  try {
    const { data } = await api.get('/checks/summary/')
    return data
  } catch { return null }
}

function rel(ts: string | null): string {
  if (!ts) return 'never'
  const s = Math.max(0, Math.round((Date.now() - new Date(ts).getTime()) / 1000))
  if (s < 60) return `${s}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  return `${Math.round(s / 3600)}h ago`
}

export default function TVOps({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data: health } = useQuery({ queryKey: ['tv-collection'], queryFn: fetchCollectionHealth, refetchInterval: 60_000, placeholderData: (p) => p })
  const { data: agents } = useQuery({ queryKey: ['tv-agents'], queryFn: fetchAgents, refetchInterval: 60_000, placeholderData: (p) => p })
  const { data: alerts } = useQuery({ queryKey: ['tv-ops-alerts'], queryFn: () => fetchAlerts('false'), refetchInterval: 60_000, placeholderData: (p) => p })
  const { data: checks } = useQuery({ queryKey: ['tv-checks'], queryFn: fetchCheckSummary, refetchInterval: 60_000, placeholderData: (p) => p })

  const w = health?.last_24h
  const rate = w?.success_rate
  const agentList = agents ?? []
  const agentsOk = agentList.filter((a) => a.status === 'active').length
  const checksOk = checks ? checks.down + checks.degraded === 0 : null

  return (
    <TVLayout title="Operations Status" refreshInterval={60} rotation={rotation}>
      <div className="flex h-full flex-col gap-6">
        <div className="grid grid-cols-4 gap-6">
          <TVStat label="Collection" value={rate == null ? '—' : `${rate}%`} color={rate == null ? TV.muted : rate >= 95 ? TV.success : rate >= 80 ? TV.warning : TV.error} />
          <TVStat label="Agents" value={`${agentsOk}/${agentList.length}`} color={agentsOk === agentList.length ? TV.success : TV.warning} />
          <TVStat label="Services" value={checks ? (checksOk ? 'All OK' : `${checks.down + checks.degraded} bad`) : '—'} color={checksOk ? TV.success : TV.warning} />
          <TVStat label="Alerts" value={alerts?.length ?? 0} color={(alerts?.length ?? 0) > 0 ? TV.warning : TV.success} />
        </div>
        <div className="grid flex-1 grid-cols-2 gap-6 overflow-hidden">
          <TVPanel title="Config Collection (24h)" className="overflow-auto">
            <div className="text-2xl">✅ {w?.success ?? 0} success &nbsp; ❌ {w?.failed ?? 0} failed</div>
            {(health?.devices_failing ?? []).length > 0 && (
              <div className="mt-3 text-lg" style={{ color: TV.muted }}>
                Failing: {(health?.devices_failing ?? []).slice(0, 6).map((d) => d.hostname).join(', ')}
              </div>
            )}
            {(health?.unsaved_configs ?? 0) > 0 && (
              <div className="mt-2 text-lg" style={{ color: TV.warning }}>{health?.unsaved_configs} unsaved config(s)</div>
            )}
          </TVPanel>
          <TVPanel title="Agent Heartbeats" className="overflow-auto">
            <div className="space-y-2 text-xl">
              {agentList.map((a) => (
                <div key={a.id} className="flex justify-between">
                  <span>{a.status === 'active' ? '✅' : '🔴'} {a.hostname}</span>
                  <span style={{ color: TV.muted }}>{rel(a.last_seen)}</span>
                </div>
              ))}
              {agentList.length === 0 && <div style={{ color: TV.muted }}>No agents enrolled.</div>}
            </div>
          </TVPanel>
        </div>
      </div>
    </TVLayout>
  )
}
