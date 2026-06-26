import clsx from 'clsx'

// Shared 1h/6h/24h/7d toggle used by the device telemetry charts and the server
// detail charts so the two can't drift. The backend metric/history endpoints
// accept these same range tokens.
export const TIME_RANGES = ['1h', '6h', '24h', '7d'] as const
export type TimeRange = (typeof TIME_RANGES)[number]

// Human-friendly suffix for chart titles, e.g. `CPU — ${RANGE_LABEL[range]}`.
export const RANGE_LABEL: Record<TimeRange, string> = {
  '1h': 'last hour',
  '6h': 'last 6 hours',
  '24h': 'last 24 hours',
  '7d': 'last 7 days',
}

export default function TimeRangeSelector({
  value,
  onChange,
  className,
}: {
  value: TimeRange
  onChange: (r: TimeRange) => void
  className?: string
}) {
  return (
    <div className={clsx('flex gap-1', className)}>
      {TIME_RANGES.map((r) => (
        <button
          key={r}
          onClick={() => onChange(r)}
          className={clsx('px-2 py-1 text-xs rounded-md border',
            value === r
              ? 'border-blue-600 text-blue-700 dark:text-blue-300 bg-blue-50 dark:bg-blue-950'
              : 'border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800')}
        >
          {r}
        </button>
      ))}
    </div>
  )
}
