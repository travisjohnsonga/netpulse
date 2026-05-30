import { useEffect, useState } from 'react'
import { SectionHeader } from '../Settings'
import { fetchPollingSettings, savePollingSettings, type PollingSettings } from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

const INTERVAL_FIELDS: { key: keyof PollingSettings; label: string; hint: string }[] = [
  { key: 'device_metrics_interval',    label: 'Device metrics (CPU, memory, temp)', hint: 'CPU, memory, environmentals' },
  { key: 'interface_traffic_interval', label: 'Interface traffic counters',         hint: 'in/out bps, errors, discards' },
  { key: 'interface_status_interval',  label: 'Interface status',                   hint: 'oper/admin up-down state' },
  { key: 'bgp_interval',               label: 'BGP peer state',                     hint: 'neighbor session state' },
  { key: 'inventory_interval',         label: 'Inventory',                          hint: 'modules, serials, transceivers' },
  { key: 'lldp_interval',              label: 'LLDP neighbors',                     hint: 'topology adjacency' },
]

const SESSION_FIELDS: { key: keyof PollingSettings; label: string; hint: string }[] = [
  { key: 'max_concurrent_sessions',   label: 'Max concurrent SNMP sessions', hint: 'parallel pollers per worker' },
  { key: 'snmp_timeout',              label: 'SNMP timeout (seconds)',       hint: 'per-request timeout' },
  { key: 'snmp_retries',             label: 'SNMP retries',                  hint: 'retries before marking failed' },
  { key: 'bulk_get_max_repetitions', label: 'Bulk-get max repetitions',      hint: 'rows per GETBULK request' },
]

export default function Polling() {
  const [settings, setSettings] = useState<PollingSettings | null>(null)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchPollingSettings().then(setSettings).catch(() => setError('Failed to load polling settings.'))
  }, [])

  const setNum = (key: keyof PollingSettings, v: string) =>
    setSettings((s) => (s ? { ...s, [key]: Number(v) } as PollingSettings : s))

  const save = async () => {
    if (!settings) return
    setSaving(true); setError(null)
    try {
      const updated = await savePollingSettings(settings)
      setSettings(updated)
      setSaved(true); setTimeout(() => setSaved(false), 2000)
    } catch {
      setError('Failed to save polling settings.')
    } finally {
      setSaving(false)
    }
  }

  if (error && !settings) return <div className="text-sm text-red-600">{error}</div>
  if (!settings) return <div className="text-sm text-gray-400">Loading…</div>

  return (
    <div>
      <SectionHeader
        title="Polling"
        description="Global SNMP polling intervals and session limits. Devices can override intervals individually on their Telemetry tab."
      />

      {error && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 mb-4 max-w-2xl">{error}</div>}

      <div className="bg-white rounded-lg border border-gray-200 p-5 mb-4 max-w-2xl">
        <h3 className="text-sm font-semibold text-gray-800 mb-1">Collection intervals (seconds)</h3>
        <p className="text-xs text-gray-400 mb-4">How often each metric class is polled by default. Lower = fresher data, more load.</p>
        <div className="grid sm:grid-cols-2 gap-4">
          {INTERVAL_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="block text-sm font-medium text-gray-700 mb-1">{f.label}</label>
              <input className={inputCls} type="number" min={5} value={settings[f.key] as number} onChange={(e) => setNum(f.key, e.target.value)} />
              <p className="text-xs text-gray-400 mt-1">{f.hint}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="bg-white rounded-lg border border-gray-200 p-5 mb-4 max-w-2xl">
        <h3 className="text-sm font-semibold text-gray-800 mb-1">SNMP session parameters</h3>
        <p className="text-xs text-gray-400 mb-4">Connection-level tuning applied across all SNMP polls.</p>
        <div className="grid sm:grid-cols-2 gap-4">
          {SESSION_FIELDS.map((f) => (
            <div key={f.key}>
              <label className="block text-sm font-medium text-gray-700 mb-1">{f.label}</label>
              <input className={inputCls} type="number" min={1} value={settings[f.key] as number} onChange={(e) => setNum(f.key, e.target.value)} />
              <p className="text-xs text-gray-400 mt-1">{f.hint}</p>
            </div>
          ))}
          <label className="flex items-center gap-2 text-sm text-gray-700 sm:col-span-2 mt-1">
            <input type="checkbox" checked={settings.bulk_get_enabled} onChange={(e) => setSettings((s) => (s ? { ...s, bulk_get_enabled: e.target.checked } : s))} />
            Use SNMP bulk-get (GETBULK) where supported
          </label>
        </div>
      </div>

      <button
        onClick={save}
        disabled={saving}
        className={`px-4 py-2 text-sm rounded-lg font-medium text-white transition-colors disabled:opacity-50 ${saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'}`}
      >
        {saving ? 'Saving…' : saved ? 'Saved!' : 'Save Changes'}
      </button>
    </div>
  )
}
