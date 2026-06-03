// Tiny inline SVG sparkline for 1h ping latency — no axes/labels, just the
// shape. null values render as gaps (device was unreachable / no data).
export default function PingSparkline({
  data, width = 80, height = 24, color = '#10b981',
}: {
  data: (number | null)[]
  width?: number
  height?: number
  color?: string
}) {
  const vals = data.filter((v): v is number => v != null)
  if (vals.length < 2) return <span className="text-gray-300 dark:text-gray-600 text-xs">—</span>

  const min = Math.min(...vals)
  const max = Math.max(...vals)
  const range = max - min || 1
  const n = data.length
  const xOf = (i: number) => (n <= 1 ? 0 : (i / (n - 1)) * (width - 2) + 1)
  const yOf = (v: number) => height - 1 - ((v - min) / range) * (height - 2)

  // Split into contiguous segments, breaking at gaps (nulls).
  const segments: string[] = []
  let cur: string[] = []
  data.forEach((v, i) => {
    if (v == null) {
      if (cur.length) { segments.push(cur.join(' ')); cur = [] }
    } else {
      cur.push(`${xOf(i).toFixed(1)},${yOf(v).toFixed(1)}`)
    }
  })
  if (cur.length) segments.push(cur.join(' '))

  return (
    <svg width={width} height={height} aria-hidden className="block">
      {segments.map((pts, i) => (
        <polyline key={i} points={pts} fill="none" stroke={color}
          strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      ))}
    </svg>
  )
}

// green <10ms · amber 10-50ms · red >50ms · gray when unknown.
export function pingColor(ms: number | null | undefined): string {
  if (ms == null) return '#9ca3af'
  if (ms < 10) return '#10b981'
  if (ms <= 50) return '#f59e0b'
  return '#ef4444'
}
