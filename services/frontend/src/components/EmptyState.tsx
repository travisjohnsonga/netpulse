import { BTN_PRIMARY } from '../lib/ui'

interface Props {
  title: string
  description: string
  action?: { label: string; onClick: () => void }
  // Optional emoji — kept for back-compat but NO LONGER rendered (de-cheesing,
  // same direction as the text-only nav). Callers can drop it entirely.
  icon?: string
}

export default function EmptyState({ title, description, action }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-6 text-center">
      <h3 className="text-base font-semibold text-gray-700 dark:text-gray-300 mb-1.5">{title}</h3>
      <p className="text-sm text-gray-500 dark:text-gray-300 max-w-sm mb-6">{description}</p>
      {action && (
        <button onClick={action.onClick} className={BTN_PRIMARY}>{action.label}</button>
      )}
    </div>
  )
}
