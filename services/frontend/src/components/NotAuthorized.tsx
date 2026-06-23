import { Link } from 'react-router-dom'

/**
 * Shown in place of a page the current user lacks the capability for (route
 * guard) — a clean, oriented dead-end rather than a broken empty shell. The API
 * 403 is the real boundary; this is the friendly face of it.
 */
export default function NotAuthorized({ detail }: { detail?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-24 px-6 text-center">
      <div className="w-14 h-14 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center text-2xl" aria-hidden>
        🔒
      </div>
      <h1 className="mt-5 text-xl font-semibold text-gray-900 dark:text-gray-100">
        You don't have permission to view this page
      </h1>
      <p className="mt-2 max-w-md text-sm text-gray-500 dark:text-gray-400">
        {detail || 'Your role doesn’t include the capability this page requires. Ask an administrator if you need access.'}
      </p>
      <Link
        to="/dashboard"
        className="mt-6 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium"
      >
        Back to dashboard
      </Link>
    </div>
  )
}
