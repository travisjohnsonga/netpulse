import { useState } from 'react'
import { SectionHeader } from '../Settings'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

// Platform-level preferences. Persisted to a settings API in a later phase;
// stored locally for now so the form behaves.

const TIMEZONES = ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Singapore', 'Australia/Sydney']

export default function General() {
  const [name, setName] = useState('NetPulse')
  const [tz, setTz] = useState('UTC')
  const [retention, setRetention] = useState('90')
  const [saved, setSaved] = useState(false)

  const save = () => { setSaved(true); setTimeout(() => setSaved(false), 2000) }

  return (
    <div>
      <SectionHeader title="General" description="Platform name, timezone and default data retention." />

      <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 space-y-4 max-w-xl">
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Platform name</label>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} />
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">Shown in the UI header, emails and reports.</p>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default timezone</label>
          <select className={inputCls} value={tz} onChange={(e) => setTz(e.target.value)}>
            {TIMEZONES.map((z) => <option key={z}>{z}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Default telemetry retention (days)</label>
          <input className={inputCls} type="number" value={retention} onChange={(e) => setRetention(e.target.value)} />
          <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">Per-data-type overrides live under System → Data retention.</p>
        </div>
        <button
          onClick={save}
          className={`px-4 py-2 text-sm rounded-lg font-medium text-white transition-colors ${saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'}`}
        >
          {saved ? 'Saved!' : 'Save Changes'}
        </button>
      </div>
    </div>
  )
}
