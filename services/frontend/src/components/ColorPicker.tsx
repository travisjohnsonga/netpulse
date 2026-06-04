import clsx from 'clsx'

// 12 preset colours offered in the swatch grid (Tailwind palette values).
export const PRESET_COLORS = [
  '#ef4444', '#f97316', '#f59e0b', '#84cc16',
  '#10b981', '#06b6d4', '#3b82f6', '#8b5cf6',
  '#ec4899', '#64748b', '#6366f1', '#14b8a6',
]

/** Simple colour picker: a 12-swatch preset grid plus a hex input for custom. */
export default function ColorPicker({ value, onChange }: {
  value: string
  onChange: (hex: string) => void
}) {
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-6 gap-2">
        {PRESET_COLORS.map((c) => (
          <button
            key={c}
            type="button"
            onClick={() => onChange(c)}
            style={{ backgroundColor: c }}
            aria-label={c}
            className={clsx(
              'w-8 h-8 rounded-md transition-transform hover:scale-110',
              value.toLowerCase() === c.toLowerCase()
                ? 'ring-2 ring-offset-2 ring-gray-800 dark:ring-gray-200 dark:ring-offset-gray-800'
                : 'ring-1 ring-black/10',
            )}
          />
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className="w-9 h-9 rounded border border-gray-300 dark:border-gray-600 bg-transparent cursor-pointer"
          aria-label="Custom colour"
        />
        <input
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="#6366f1"
          className="w-28 px-2 py-1.5 text-sm font-mono border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100"
        />
      </div>
    </div>
  )
}
