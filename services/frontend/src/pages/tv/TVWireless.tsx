import { useQuery } from '@tanstack/react-query'
import TVLayout, { TV, TVStat, TVPanel } from './TVLayout'
import { fetchWirelessSummary } from '../../api/client'

export default function TVWireless({ rotation }: { rotation?: Parameters<typeof TVLayout>[0]['rotation'] }) {
  const { data } = useQuery({ queryKey: ['tv-wireless'], queryFn: fetchWirelessSummary, refetchInterval: 45_000, placeholderData: (p) => p })
  const aps = data?.aps ?? []

  return (
    <TVLayout title="Wireless Overview" refreshInterval={45} rotation={rotation}>
      <div className="flex h-full flex-col gap-6">
        <div className="grid grid-cols-4 gap-6">
          <TVStat label="APs Online" value={`${data?.online ?? 0}/${data?.total_aps ?? 0}`} color={data && data.online === data.total_aps ? TV.success : TV.warning} />
          <TVStat label="Offline" value={data?.offline ?? 0} color={(data?.offline ?? 0) > 0 ? TV.error : TV.success} />
          <TVStat label="Clients" value={data?.total_clients ?? 0} color={TV.accent} />
          <TVStat label="Satisfaction" value={data?.avg_satisfaction == null ? '—' : `${data.avg_satisfaction}%`} />
        </div>
        <TVPanel title="Access Points" className="flex-1 overflow-auto">
          <div className="grid grid-cols-2 gap-x-10 gap-y-2 text-xl">
            {aps.map((ap) => {
              const online = ap.state === 1
              return (
                <div key={ap.device_id} className="flex items-center justify-between">
                  <span><span style={{ color: online ? TV.success : TV.error }}>{online ? 'UP' : 'DOWN'}</span> {ap.hostname}</span>
                  <span style={{ color: TV.muted }}>
                    {online ? `${ap.client_count ?? 0} clients` : 'OFFLINE'}
                    {online && ap.satisfaction != null ? `  ·  ${ap.satisfaction}%` : ''}
                  </span>
                </div>
              )
            })}
            {aps.length === 0 && <div style={{ color: TV.muted }}>No access points.</div>}
          </div>
        </TVPanel>
      </div>
    </TVLayout>
  )
}
