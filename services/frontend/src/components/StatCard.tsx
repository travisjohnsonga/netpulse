import { Link } from 'react-router-dom'
import clsx from 'clsx'

interface Props {
  title: string
  value: string | number
  subtitle?: string
  color?: 'blue' | 'green' | 'red' | 'yellow'
  action?: { label: string; href: string }
}

const borderColors: Record<NonNullable<Props['color']>, string> = {
  blue: 'border-blue-500',
  green: 'border-green-500',
  red: 'border-red-500',
  yellow: 'border-yellow-500',
}

const valueColors: Record<NonNullable<Props['color']>, string> = {
  blue: 'text-blue-600',
  green: 'text-green-600',
  red: 'text-red-600',
  yellow: 'text-yellow-600',
}

export default function StatCard({
  title,
  value,
  subtitle,
  color = 'blue',
  action,
}: Props) {
  return (
    <div
      className={clsx(
        'bg-white dark:bg-gray-800 rounded-lg shadow-sm border-t-4 p-5 flex flex-col gap-1',
        borderColors[color],
      )}
    >
      <span className="text-sm font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">
        {title}
      </span>
      <span className={clsx('text-3xl font-bold', valueColors[color])}>
        {value}
      </span>
      {subtitle && (
        <span className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">{subtitle}</span>
      )}
      {action && (
        <Link
          to={action.href}
          className="mt-2 text-sm font-medium text-blue-600 hover:text-blue-800 transition-colors"
        >
          {action.label} &rarr;
        </Link>
      )}
    </div>
  )
}
