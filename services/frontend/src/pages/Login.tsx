import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { login, fetchSSOProvidersPublic, type SSOProviderPublic } from '../api/client'
import { useAuthStore } from '../store/authStore'

/** Brand-ish icon for each SSO provider (lucide-react isn't a dependency). */
function providerIcon(provider: string): ReactNode {
  switch (provider) {
    case 'google-oauth2':
      return <span className="text-base font-bold text-[#4285F4]">G</span>
    case 'azuread-tenant-oauth2':
      return (
        <svg viewBox="0 0 23 23" className="w-4 h-4" aria-hidden>
          <rect width="10" height="10" x="1" y="1" fill="#F25022" />
          <rect width="10" height="10" x="12" y="1" fill="#7FBA00" />
          <rect width="10" height="10" x="1" y="12" fill="#00A4EF" />
          <rect width="10" height="10" x="12" y="12" fill="#FFB900" />
        </svg>
      )
    case 'okta-oauth2':
      return <span className="text-base font-bold text-[#007DC1]">O</span>
    case 'github':
      return (
        <svg viewBox="0 0 16 16" className="w-4 h-4 fill-current" aria-hidden>
          <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 012-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
        </svg>
      )
    default:
      return <span className="text-base" aria-hidden>🔐</span>
  }
}

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const setTokens = useAuthStore((s) => s.setTokens)

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [ssoProviders, setSsoProviders] = useState<SSOProviderPublic[]>([])

  const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? '/dashboard'

  // Capture JWTs handed back by the SSO flow. The backend redirects to
  // `/#token=<access>&refresh=<refresh>` (SOCIAL_AUTH_LOGIN_REDIRECT_URL →
  // /api/sso/jwt/), or `?sso_error=...` on failure. Store via the auth store
  // (same as local login), then scrub the URL.
  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const query = new URLSearchParams(window.location.search)
    const token = hash.get('token') || query.get('token')
    const refresh = hash.get('refresh') || query.get('refresh')
    const ssoError = hash.get('sso_error') || query.get('sso_error')

    if (token) {
      setTokens(token, refresh ?? '')
      window.history.replaceState({}, '', '/')
      navigate('/dashboard', { replace: true })
    } else if (ssoError) {
      setError('SSO sign-in failed. Try again, or sign in with a username below.')
      window.history.replaceState({}, '', '/login')
    }
  }, [navigate, setTokens])

  // Load enabled SSO providers for the buttons (public endpoint; best-effort).
  useEffect(() => {
    fetchSSOProvidersPublic()
      .then(setSsoProviders)
      .catch(() => setSsoProviders([]))
  }, [])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (!username || !password) { setError('Username and password are required.'); return }
    setLoading(true)
    setError(null)
    try {
      const { access, refresh } = await login(username, password)
      setTokens(access, refresh)
      navigate(from, { replace: true })
    } catch {
      setError('Invalid username or password.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-900 to-gray-800 flex items-center justify-center p-4">
      <div className="w-full max-w-sm">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="w-14 h-14 bg-blue-500 rounded-2xl flex items-center justify-center mx-auto mb-4 shadow-lg">
            <span className="text-white font-bold text-xl">NP</span>
          </div>
          <h1 className="text-2xl font-bold text-white">NetPulse</h1>
          <p className="text-gray-400 text-sm mt-1">Network Intelligence Platform</p>
        </div>

        {/* Card */}
        <div className="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl p-8">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100 mb-6">Sign in</h2>

          {error && (
            <div className="mb-4 px-4 py-3 bg-red-50 dark:bg-red-950 border border-red-200 dark:border-red-900 rounded-lg text-sm text-red-700 dark:text-red-300">
              {error}
            </div>
          )}

          {/* SSO providers (shown above the local form when configured) */}
          {ssoProviders.length > 0 && (
            <div className="mb-6">
              <div className="space-y-3">
                {ssoProviders.map((provider) => (
                  <a
                    key={provider.id}
                    href={provider.login_url}
                    className="flex items-center justify-center w-full px-4 py-2.5 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700 transition text-sm font-medium gap-3 text-gray-700 dark:text-gray-200"
                  >
                    {providerIcon(provider.provider)}
                    Sign in with {provider.name}
                  </a>
                ))}
              </div>

              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-300 dark:border-gray-600" />
                </div>
                <div className="relative flex justify-center text-xs text-gray-500">
                  <span className="px-2 bg-white dark:bg-gray-900">or sign in with username</span>
                </div>
              </div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Username
              </label>
              <input
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full px-3 py-2.5 border border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="admin"
                disabled={loading}
              />
            </div>

            <div>
              <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
                Password
              </label>
              <input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full px-3 py-2.5 border border-gray-300 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-100 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
                placeholder="••••••••"
                disabled={loading}
              />
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white rounded-lg text-sm font-medium transition-colors mt-2 flex items-center justify-center gap-2"
            >
              {loading && (
                <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              )}
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="text-center text-xs text-gray-500 mt-6">
          NetPulse — open source network intelligence
        </p>
      </div>
    </div>
  )
}
