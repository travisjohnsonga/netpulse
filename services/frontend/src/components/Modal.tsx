import { type ReactNode } from 'react'
import clsx from 'clsx'

interface Props {
  title: string
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** Tailwind max-width class for the dialog. Defaults to `max-w-md`. */
  size?: 'md' | 'lg' | 'xl'
}

const SIZES = { md: 'max-w-md', lg: 'max-w-lg', xl: 'max-w-2xl' } as const

export default function Modal({ title, onClose, children, footer, size = 'md' }: Props) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50"
      onClick={onClose}
    >
      <div
        className={clsx('bg-white rounded-xl shadow-xl w-full flex flex-col max-h-[90vh]', SIZES[size])}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
          <h2 className="text-lg font-bold text-gray-900">{title}</h2>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-xl leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="px-6 py-5 overflow-y-auto">{children}</div>
        {footer && (
          <div className="flex gap-3 px-6 py-4 border-t border-gray-200">{footer}</div>
        )}
      </div>
    </div>
  )
}
