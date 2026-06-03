import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import App from './App'
import { useAuthStore } from './store/authStore'
import './index.css'

// Capture the JWT handed back by the SSO flow (backend redirects to
// `/#token=<access>&refresh=<refresh>`) BEFORE React renders. If we waited for a
// component effect, the router's "/" → "/login" redirect would drop the hash
// fragment first and the user would land back on the login page. Storing the
// token here means the first render already sees an authenticated session.
;(function captureSSOTokens() {
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const query = new URLSearchParams(window.location.search)
  const token = hash.get('token') || query.get('token')
  const refresh = hash.get('refresh') || query.get('refresh')
  if (token) {
    useAuthStore.getState().setTokens(token, refresh ?? '')
    // Scrub credentials from the URL and land on the app root.
    window.history.replaceState({}, '', '/')
  }
})()

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>,
)
