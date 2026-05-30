import { useEffect, useState } from 'react'
import clsx from 'clsx'
import { SectionHeader } from '../Settings'
import ConfigBackupSection from './ConfigBackupSection'
import CredentialBlock from '../../components/CredentialBlock'
import {
  fetchCVEFeedSettings, saveCVEFeedSettings,
  type CVEFeedSettings, type CVEFeedSettingsWrite,
} from '../../api/client'

const FEEDS: { key: keyof CVEFeedSettings; name: string; description: string }[] = [
  { key: 'nvd_enabled', name: 'NVD (NIST)', description: 'National Vulnerability Database CVE feed' },
  { key: 'cisa_kev_enabled', name: 'CISA KEV', description: 'Known Exploited Vulnerabilities catalog' },
  { key: 'cisco_psirt_enabled', name: 'Cisco PSIRT', description: 'Cisco security advisories (openVuln API)' },
  { key: 'paloalto_enabled', name: 'Palo Alto', description: 'Palo Alto Networks security advisories' },
]

export default function DataSources() {
  const [settings, setSettings] = useState<CVEFeedSettings | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = () => fetchCVEFeedSettings().then(setSettings).catch(() => setError('Failed to load feed settings.'))
  useEffect(() => { load() }, [])

  // Persist a change and refetch so has_* flags / toggles reflect the server.
  const save = async (payload: CVEFeedSettingsWrite) => {
    const updated = await saveCVEFeedSettings(payload)
    setSettings(updated)
  }

  const toggle = (key: keyof CVEFeedSettings) => {
    if (!settings) return
    save({ [key]: !settings[key] } as CVEFeedSettingsWrite).catch(() => setError('Failed to update feed.'))
  }

  return (
    <div>
      <SectionHeader title="Data Sources" description="CVE intelligence and end-of-life data feeds." />

      {error && <div className="bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-sm text-red-700 mb-4 max-w-xl">{error}</div>}

      {/* Feed toggles */}
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden divide-y divide-gray-100 mb-6">
        {FEEDS.map((f) => {
          const enabled = !!settings?.[f.key]
          return (
            <div key={f.key} className="flex items-center gap-4 px-5 py-3">
              <div className="flex-1 min-w-0">
                <p className="font-medium text-gray-800">{f.name}</p>
                <p className="text-xs text-gray-500">{f.description}</p>
              </div>
              <span className={clsx('text-xs font-medium px-2 py-0.5 rounded-full', enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500')}>
                {enabled ? 'Enabled' : 'Disabled'}
              </span>
              <button onClick={() => toggle(f.key)} disabled={!settings} className="px-2.5 py-1 text-xs border border-gray-300 rounded-md hover:bg-gray-50 disabled:opacity-50">
                {enabled ? 'Disable' : 'Enable'}
              </button>
            </div>
          )
        })}
      </div>

      {/* Credentials — secrets go to OpenBao; only "configured" state comes back */}
      {settings && (
        <div className="space-y-4 max-w-xl">
          <CredentialBlock
            title="NVD API Key"
            description="Raises the NVD rate limit for faster CVE ingestion."
            configured={settings.has_nvd_api_key}
            label="NVD API key configured"
            fields={[{ key: 'nvd_api_key', label: 'API Key', placeholder: 'Enter NVD API key…' }]}
            onSave={(v) => save({ nvd_api_key: v.nvd_api_key })}
          />

          <CredentialBlock
            title="Cisco PSIRT OAuth"
            description="Client ID + secret for the Cisco openVuln API."
            configured={settings.has_psirt_credentials}
            label="Cisco PSIRT credentials configured"
            fields={[
              { key: 'cisco_psirt_client_id', label: 'Client ID' },
              { key: 'cisco_psirt_client_secret', label: 'Client Secret' },
            ]}
            onSave={(v) => save({
              cisco_psirt_client_id: v.cisco_psirt_client_id,
              cisco_psirt_client_secret: v.cisco_psirt_client_secret,
            })}
          />

          <CredentialBlock
            title="Palo Alto API Key"
            description="API key for Palo Alto Networks security advisories."
            configured={settings.has_paloalto_api_key}
            label="Palo Alto API key configured"
            fields={[{ key: 'paloalto_api_key', label: 'API Key', placeholder: 'Enter Palo Alto API key…' }]}
            onSave={(v) => save({ paloalto_api_key: v.paloalto_api_key })}
          />
        </div>
      )}

      <div className="mt-8">
        <ConfigBackupSection />
      </div>
    </div>
  )
}
