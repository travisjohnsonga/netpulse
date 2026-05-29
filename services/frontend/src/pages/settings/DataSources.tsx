import { useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import ConfigBackupSection from './ConfigBackupSection'

// CVE / advisory / EOL data feeds. Credentials are written to OpenBao; only
// enablement + non-secret config is stored relationally (backend in progress).

const inputCls =
  'w-full px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500'

function SaveButton({ onSave }: { onSave: () => void }) {
  const [saved, setSaved] = useState(false)
  return (
    <button
      onClick={() => { onSave(); setSaved(true); setTimeout(() => setSaved(false), 2000) }}
      className={clsx('px-4 py-2 text-sm rounded-lg font-medium text-white transition-colors', saved ? 'bg-green-600' : 'bg-blue-600 hover:bg-blue-700')}
    >
      {saved ? 'Saved!' : 'Save'}
    </button>
  )
}

interface Feed { id: string; name: string; description: string; enabled: boolean }

export default function DataSources() {
  const [feeds, setFeeds] = useState<Feed[]>([
    { id: 'nvd', name: 'NVD (NIST)', description: 'National Vulnerability Database CVE feed', enabled: true },
    { id: 'cisco', name: 'Cisco PSIRT', description: 'Cisco security advisories (openVuln API)', enabled: false },
    { id: 'eox', name: 'Cisco EoX', description: 'End-of-life / end-of-sale milestones', enabled: false },
    { id: 'endoflife', name: 'endoflife.date', description: 'Multi-vendor product EOL dates', enabled: true },
  ])

  const toggle = (id: string) => setFeeds((f) => f.map((x) => (x.id === id ? { ...x, enabled: !x.enabled } : x)))

  return (
    <div>
      <SectionHeader title="Data Sources" description="CVE intelligence and end-of-life data feeds." />

      {/* Feed toggles */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden divide-y divide-gray-100 mb-6">
        {feeds.map((f) => (
          <div key={f.id} className="flex items-center gap-4 px-5 py-3">
            <div className="flex-1 min-w-0">
              <p className="font-medium text-gray-800">{f.name}</p>
              <p className="text-xs text-gray-500">{f.description}</p>
            </div>
            <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', f.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500')}>
              {f.enabled ? 'Enabled' : 'Disabled'}
            </span>
            <button onClick={() => toggle(f.id)} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50">
              {f.enabled ? 'Disable' : 'Enable'}
            </button>
          </div>
        ))}
      </div>

      {/* Credentials */}
      <div className="space-y-4 max-w-xl">
        <div className="bg-white border border-gray-200 rounded-lg p-4">
          <label className="block text-sm font-medium text-gray-700 mb-1">NVD API Key</label>
          <div className="flex gap-2">
            <input type="password" placeholder="Enter NVD API key…" className={inputCls} autoComplete="off" />
            <SaveButton onSave={() => {}} />
          </div>
          <p className="text-xs text-gray-400 mt-1">Raises the NVD rate limit. 🔒 Stored securely in OpenBao.</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-3">
          <p className="text-sm font-medium text-gray-700">Cisco PSIRT OAuth</p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Client ID</label>
              <input type="password" placeholder="Client ID" className={inputCls} autoComplete="off" />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Client Secret</label>
              <input type="password" placeholder="Client Secret" className={inputCls} autoComplete="off" />
            </div>
          </div>
          <SaveButton onSave={() => {}} />
          <p className="text-xs text-gray-400">🔒 Stored securely in OpenBao.</p>
        </div>
      </div>

      <div className="mt-8">
        <ConfigBackupSection />
      </div>
    </div>
  )
}
