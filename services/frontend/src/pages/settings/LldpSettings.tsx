import { useEffect, useState } from 'react'
import { SectionHeader } from '../Settings'
import { fetchLldpSettings, saveLldpSettings, type LldpSettings as LldpSettingsT } from '../../api/client'
import { CAP_META, CAP_OPTIONS, capLabel } from '../../lib/lldpCapabilities'

// Lets admins choose which LLDP capabilities are hidden by default from the
// "LLDP Neighbors — Not in Inventory" list (e.g. IP phones, PCs, cable modems).
// A capability is HIDDEN when checked here. Persisted server-side and used as
// the default exclusion for the undiscovered endpoint + sidebar badge.
export default function LldpSettings() {
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [settings, setSettings] = useState<LldpSettingsT | null>(null)
  const [excluded, setExcluded] = useState<string[]>([])

  useEffect(() => {
    fetchLldpSettings()
      .then((s) => { setSettings(s); setExcluded(s.exclude_capabilities) })
      .catch(() => setError('Failed to load LLDP settings.'))
      .finally(() => setLoading(false))
  }, [])

  // Show every known capability, but list any unexpected stored tokens too.
  const options = settings
    ? [...CAP_OPTIONS, ...settings.available_capabilities.filter((c) => !CAP_OPTIONS.includes(c as never))]
    : [...CAP_OPTIONS]

  const toggle = (cap: string) => setExcluded((cur) =>
    cur.includes(cap) ? cur.filter((c) => c !== cap) : [...cur, cap])

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const s = await saveLldpSettings(excluded)
      setSettings(s); setExcluded(s.exclude_capabilities)
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Failed to save LLDP settings.')
    } finally {
      setSaving(false)
    }
  }

  const resetToDefault = () => settings && setExcluded(settings.default_exclude_capabilities)

  const dirty = settings
    ? [...excluded].sort().join(',') !== [...settings.exclude_capabilities].sort().join(',')
    : false

  return (
    <div>
      <SectionHeader
        title="LLDP Neighbors"
        description="Choose which device types are hidden by default from the “Not in Inventory” list. These are typically unmanaged endpoints you don’t track as devices."
      />

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 max-w-xl space-y-4">
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">Hide these capabilities by default</h3>
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
            Checked capabilities are excluded from the undiscovered-neighbors list and the sidebar count.
            Users can still reveal them per-session from the page’s filter bar.
          </p>
        </div>

        {loading ? (
          <div className="text-sm text-gray-400">Loading…</div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {options.map((cap) => (
              <label key={cap} className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer select-none rounded-md px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-700/40">
                <input
                  type="checkbox"
                  checked={excluded.includes(cap)}
                  onChange={() => toggle(cap)}
                  className="rounded border-gray-300 dark:border-gray-600 text-blue-600 focus:ring-blue-500"
                />
                <span aria-hidden>{CAP_META[cap]?.icon ?? '•'}</span>
                <span>{capLabel(cap)}</span>
                <span className="text-gray-300 dark:text-gray-600 text-xs font-mono">{cap}</span>
              </label>
            ))}
          </div>
        )}

        {error && <div className="text-sm text-red-600 dark:text-red-400">{error}</div>}

        <div className="flex items-center gap-3 pt-1">
          <button
            onClick={save}
            disabled={saving || !dirty}
            className={`px-4 py-2 text-sm rounded-lg font-medium text-white transition-colors disabled:opacity-50 ${saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'}`}
          >
            {saved ? 'Saved!' : saving ? 'Saving…' : 'Save Changes'}
          </button>
          <button
            onClick={resetToDefault}
            disabled={loading}
            className="text-xs text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-50"
          >
            Reset to defaults
          </button>
        </div>
      </div>
    </div>
  )
}
