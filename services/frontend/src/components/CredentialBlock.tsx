import { useState } from 'react'
import clsx from 'clsx'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 dark:bg-gray-900 dark:text-gray-100'

export interface CredentialField {
  key: string
  label: string
  placeholder?: string
}

/**
 * Reusable secret-entry block. When a credential is already configured it shows
 * a status card ("Stored securely in OpenBao") with an Update button; otherwise
 * (or while editing) it shows password inputs. Secrets are never prefilled — the
 * inputs always start blank since the API never returns stored secret values.
 */
export default function CredentialBlock({ title, description, configured, label, fields, onSave }: {
  title: string
  description?: string
  configured: boolean
  label: string
  fields: CredentialField[]
  onSave: (values: Record<string, string>) => Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [values, setValues] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const showForm = !configured || editing

  const reset = () => { setValues({}); setError(null) }

  const save = async () => {
    setSaving(true); setError(null)
    try {
      await onSave(values)
      reset(); setEditing(false)
    } catch {
      setError('Failed to save. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
      <div className="flex items-center justify-between mb-1">
        <p className="text-sm font-medium text-gray-700 dark:text-gray-300">{title}</p>
      </div>
      {description && <p className="text-xs text-gray-400 dark:text-gray-500 mb-3">{description}</p>}

      {!showForm ? (
        <div className="flex items-center justify-between gap-3 bg-green-50 dark:bg-green-900/30 border border-green-200 dark:border-green-700 rounded-lg px-3 py-2.5">
          <div className="min-w-0">
            <p className="text-sm font-medium text-green-800 dark:text-green-400 truncate">✅ {label}</p>
            <p className="text-xs text-green-700 dark:text-green-400">🔒 Stored securely in OpenBao</p>
          </div>
          <button onClick={() => { reset(); setEditing(true) }}
            className="shrink-0 px-2.5 py-1 text-xs border border-green-300 dark:border-green-700 bg-white dark:bg-gray-800 rounded-md hover:bg-green-100 dark:hover:bg-green-900/50 text-green-800 dark:text-green-400 font-medium">
            Update Key
          </button>
        </div>
      ) : (
        <div className="space-y-3">
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className={clsx('grid gap-3', fields.length > 1 ? 'grid-cols-2' : 'grid-cols-1')}>
            {fields.map((f) => (
              <div key={f.key}>
                {fields.length > 1 && <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">{f.label}</label>}
                <input
                  type="password" autoComplete="off"
                  className={inputCls}
                  placeholder={f.placeholder || f.label}
                  value={values[f.key] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [f.key]: e.target.value }))}
                />
              </div>
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={save} disabled={saving}
              className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded-lg font-medium">
              {saving ? 'Saving…' : 'Save'}
            </button>
            {configured && (
              <button onClick={() => { reset(); setEditing(false) }}
                className="px-4 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50 dark:text-gray-300">
                Cancel
              </button>
            )}
          </div>
          <p className="text-xs text-gray-400 dark:text-gray-500">🔒 Stored securely in OpenBao — never displayed after saving.</p>
        </div>
      )}
    </div>
  )
}
