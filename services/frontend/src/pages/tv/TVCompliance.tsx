import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVStat, TVPanel } from './TVLayout'
import { fetchFrameworks, fetchCollectionHealth } from '../../api/client'

function gradeFor(pct: number | null): { grade: string; color: string } {
  if (pct == null) return { grade: '—', color: TV.muted }
  if (pct >= 90) return { grade: 'A', color: TV.success }
  if (pct >= 80) return { grade: 'B', color: TV.success }
  if (pct >= 70) return { grade: 'C', color: TV.warning }
  if (pct >= 60) return { grade: 'D', color: TV.warning }
  return { grade: 'F', color: TV.error }
}

export default function TVCompliance({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data: frameworks } = useQuery({ queryKey: ['tv-frameworks'], queryFn: fetchFrameworks, refetchInterval: 300_000, placeholderData: (p) => p })
  const { data: health } = useQuery({ queryKey: ['tv-compliance-health'], queryFn: fetchCollectionHealth, refetchInterval: 300_000, placeholderData: (p) => p })

  const fws = frameworks ?? []
  const covs = fws.map((f) => f.coverage).filter((c): c is number => c != null)
  const fleet = covs.length ? Math.round(covs.reduce((a, b) => a + b, 0) / covs.length) : null
  const g = gradeFor(fleet)

  return (
    <TVLayout title="Compliance Status" refreshInterval={300} rotation={rotation}>
      <div className="flex h-full flex-col gap-6">
        <div className="grid grid-cols-4 gap-6">
          <TVStat label="Fleet Coverage" value={fleet == null ? '—' : `${fleet}%`} sub={`Grade ${g.grade}`} color={g.color} />
          <TVStat label="Frameworks" value={fws.length} />
          <TVStat label="Unsaved Configs" value={health?.unsaved_configs ?? 0} color={(health?.unsaved_configs ?? 0) > 0 ? TV.warning : TV.success} />
          <TVStat label="Never Collected" value={health?.devices_never_collected ?? 0} color={(health?.devices_never_collected ?? 0) > 0 ? TV.warning : TV.success} />
        </div>
        <TVPanel title="Framework Coverage" className="flex-1 overflow-auto">
          <div className="space-y-3 text-xl">
            {fws.map((f) => {
              const fg = gradeFor(f.coverage)
              return (
                <div key={f.key} className="flex items-center gap-4">
                  <span className="w-64 truncate">{f.name}</span>
                  <div className="h-3 flex-1 rounded-full" style={{ background: TV.bg }}>
                    <div className="h-3 rounded-full" style={{ width: `${f.coverage ?? 0}%`, background: fg.color }} />
                  </div>
                  <span className="w-20 text-right" style={{ color: fg.color }}>{f.coverage == null ? '—' : `${f.coverage}%`}</span>
                </div>
              )
            })}
            {fws.length === 0 && <div style={{ color: TV.muted }}>No frameworks configured.</div>}
          </div>
        </TVPanel>
      </div>
    </TVLayout>
  )
}
