interface Props {
  title: string
  description: string
  action?: { label: string; onClick: () => void }
  icon?: string
}

export default function EmptyState({ title, description, action, icon = '📭' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-20 px-6 text-center">
      <span className="text-5xl mb-4" role="img" aria-label="empty">
        {icon}
      </span>
      <h3 className="text-lg font-semibold text-gray-700 mb-2">{title}</h3>
      <p className="text-sm text-gray-500 max-w-sm mb-6">{description}</p>
      {action && (
        <button
          onClick={action.onClick}
          className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors shadow-sm"
        >
          {action.label}
        </button>
      )}
    </div>
  )
}
