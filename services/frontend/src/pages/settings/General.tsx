import { useEffect, useState } from 'react'
import { SectionHeader } from '../Settings'
import { fetchHostnameDisplay, saveHostnameDisplay } from '../../api/client'

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100'

// Platform-level preferences. Persisted to a settings API in a later phase;
// stored locally for now so the form behaves.

const TIMEZONES = ['UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'Europe/London', 'Europe/Berlin', 'Asia/Singapore', 'Australia/Sydney']

// Mirror of apps.core.hostname.strip_domain for the live preview.
function previewStrip(hostname: string, mode: 'strip' | 'full', suffix: string): string {
  if (mode !== 'strip') return hostname
  if (suffix) {
    const dotted = '.' + suffix
    return hostname.endsWith(dotted) ? hostname.slice(0, -dotted.length) : hostname
  }
  return hostname.includes('.') ? hostname.split('.')[0] : hostname
}

export default function General() {
  const [name, setName] = useState('spane')
  const [tz, setTz] = useState('UTC')
  const [retention, setRetention] = useState('90')
  const [saved, setSaved] = useState(false)

  // Hostname display
  const [mode, setMode] = useState<'strip' | 'full'>('full')
  const [suffix, setSuffix] = useState('')
  const [hostSaved, setHostSaved] = useState(false)
  const [hostSaving, setHostSaving] = useState(false)

  useEffect(() => {
    fetchHostnameDisplay()
      .then((d) => { setMode(d.mode); setSuffix(d.domain_suffix) })
      .catch(() => { /* fall back to defaults */ })
  }, [])

  const save = () => { setSaved(true); setTimeout(() => setSaved(false), 2000) }

  const saveHostname = async () => {
    setHostSaving(true)
    try {
      const d = await saveHostnameDisplay({ mode, domain_suffix: mode === 'strip' ? suffix.trim() : suffix })
      setMode(d.mode); setSuffix(d.domain_suffix)
      setHostSaved(true); setTimeout(() => setHostSaved(false), 2000)
    } finally {
      setHostSaving(false)
    }
  }

  const sampleFull = suffix ? `router1.${suffix}` : 'router1.dnstest.local'
  const samplePreview = previewStrip(sampleFull, mode, suffix)

  return (
    <div className="space-y-6">
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

      <div>
        <SectionHeader title="Hostname Display" description="Optionally shorten device hostnames shown in the UI. Display only — the full hostname is still used for SSH, SNMP and syslog." />

        <div className="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-5 space-y-4 max-w-xl">
          <div className="space-y-2">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="hostname-mode"
                className="mt-0.5"
                checked={mode === 'full'}
                onChange={() => setMode('full')}
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">
                <span className="font-medium">Show full hostname</span>
                <span className="block text-xs text-gray-400 dark:text-gray-500">Display hostnames exactly as stored (e.g. router1.dnstest.local).</span>
              </span>
            </label>
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="radio"
                name="hostname-mode"
                className="mt-0.5"
                checked={mode === 'strip'}
                onChange={() => setMode('strip')}
              />
              <span className="text-sm text-gray-700 dark:text-gray-300">
                <span className="font-medium">Strip domain suffix</span>
                <span className="block text-xs text-gray-400 dark:text-gray-500">Show a shorter name in lists, headers and the topology picker.</span>
              </span>
            </label>
          </div>

          {mode === 'strip' && (
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Domain suffix</label>
              <input
                className={inputCls}
                value={suffix}
                placeholder="dnstest.local"
                onChange={(e) => setSuffix(e.target.value)}
              />
              <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">Leave empty to strip everything after the first dot.</p>
            </div>
          )}

          {mode === 'strip' && (
            <div className="text-xs text-gray-500 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2">
              Preview: <span className="font-mono text-gray-700 dark:text-gray-300">{sampleFull}</span>
              <span className="mx-1.5">→</span>
              <span className="font-mono font-medium text-gray-900 dark:text-gray-100">{samplePreview}</span>
            </div>
          )}

          <button
            onClick={saveHostname}
            disabled={hostSaving}
            className={`px-4 py-2 text-sm rounded-lg font-medium text-white transition-colors disabled:opacity-60 ${hostSaved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700'}`}
          >
            {hostSaved ? 'Saved!' : hostSaving ? 'Saving…' : 'Save Changes'}
          </button>
        </div>
      </div>
    </div>
  )
}
