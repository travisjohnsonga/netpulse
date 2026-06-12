import { useEffect, useState } from 'react'
import { fetchSetupStatus, type SetupStatus } from '../api/client'

const SETUP_CMD = './scripts/setup.sh'

/**
 * First-run gate. Shown (instead of the app) while SETUP_COMPLETE is false.
 * "Check Again" polls /api/setup/status/ every 5s; once setup completes it
 * shows a success message then hands back to the app (→ /login) via onComplete.
 */
export default function SetupRequired({ status, onComplete }: {
  status: SetupStatus
  onComplete: () => void
}) {
  const [copied, setCopied] = useState(false)
  const [polling, setPolling] = useState(false)
  const [done, setDone] = useState(false)
  const [latest, setLatest] = useState<SetupStatus>(status)

  // While polling, re-check every 5s until setup completes.
  useEffect(() => {
    if (!polling || done) return
    const t = setInterval(async () => {
      try {
        const s = await fetchSetupStatus()
        setLatest(s)
        if (s.setup_complete) {
          setDone(true); setPolling(false)
          setTimeout(onComplete, 2000)
        }
      } catch { /* keep polling */ }
    }, 5000)
    return () => clearInterval(t)
  }, [polling, done, onComplete])

  const copy = () => {
    navigator.clipboard.writeText(SETUP_CMD)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1500) })
      .catch(() => {})
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 dark:bg-gray-950 px-4">
      <div className="w-full max-w-lg bg-white dark:bg-gray-900 rounded-2xl shadow-xl border border-gray-200 dark:border-gray-800 p-8 text-center">
        <div className="text-4xl mb-2">🚀</div>
        <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">Welcome to spane</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400 mt-2">
          Initial setup is required before you can use spane.
        </p>

        <div className="mt-6 text-left">
          <p className="text-xs font-medium text-gray-600 dark:text-gray-300 mb-1">Run this command on your server:</p>
          <div className="flex items-center gap-2 bg-gray-900 text-gray-100 rounded-lg px-3 py-2 font-mono text-sm">
            <span className="text-green-400">$</span>
            <span className="flex-1">{SETUP_CMD}</span>
            <button onClick={copy} className="px-2 py-0.5 text-xs border border-gray-600 rounded hover:bg-gray-800">
              {copied ? 'Copied!' : 'Copy 📋'}
            </button>
          </div>
        </div>

        <div className="mt-5 text-left text-sm text-gray-600 dark:text-gray-300">
          <p className="font-medium mb-1">Setup will:</p>
          <ul className="space-y-0.5">
            <li>✓ Initialize secure credential storage</li>
            <li>✓ Generate encryption keys</li>
            <li>✓ Create admin account</li>
            <li>✓ Configure services</li>
          </ul>
        </div>

        <div className="mt-6">
          {done ? (
            <p className="text-green-600 dark:text-green-400 font-medium">✅ Setup complete! Redirecting…</p>
          ) : (
            <button onClick={() => setPolling((p) => !p)}
              className="px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium">
              {polling ? '⏳ Checking every 5s…' : '🔄 Check Again'}
            </button>
          )}
          {!done && (
            <p className="mt-2 text-xs text-gray-400 dark:text-gray-500">
              {latest.openbao_healthy ? 'Credential storage: healthy' : 'Credential storage (OpenBao): not ready'}
              {' · '}{latest.database_healthy ? 'Database: healthy' : 'Database: not ready'}
            </p>
          )}
        </div>

        <div className="mt-6 pt-4 border-t border-gray-100 dark:border-gray-800 text-xs text-gray-400 dark:text-gray-500 space-y-1">
          <p>Already ran setup? Check that all services are running: <code className="font-mono">docker compose ps</code></p>
          <p>Need help? <a href="https://github.com/travisjohnsonga/netpulse" target="_blank" rel="noreferrer" className="text-blue-600 hover:text-blue-800">github.com/travisjohnsonga/netpulse</a></p>
        </div>
      </div>
    </div>
  )
}

/** Persistent banner shown when setup is complete but OpenBao is unreachable. */
export function OpenBaoDegradedBanner() {
  return (
    <div className="bg-amber-50 dark:bg-amber-900/30 border-b border-amber-200 dark:border-amber-800 px-4 py-2 text-sm text-amber-800 dark:text-amber-300 text-center">
      ⚠️ Credential storage (OpenBao) is unavailable. Credential operations will fail. Run <code className="font-mono">./scripts/setup.sh</code> to re-initialize.
    </div>
  )
}
