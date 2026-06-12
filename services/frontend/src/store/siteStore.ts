import { create } from 'zustand'
import { fetchSites, type Site } from '../api/client'

// Global site filter. The selected site id is kept as a string (matching the
// id used in `?site=` query params and the per-page filters) so `null` cleanly
// means "All Sites". Persisted to localStorage so the choice survives refresh.
const STORAGE_KEY = 'selectedSite'

interface SiteState {
  /** Selected site id as a string, or null for "All Sites". */
  selectedSite: string | null
  /** Site list, loaded once when the app shell mounts. */
  sites: Site[]
  /** Set (or clear, with null) the active site; persisted to localStorage. */
  setSelectedSite: (id: string | null) => void
  /** Load the site list (idempotent; safe to call on every shell mount). */
  loadSites: () => Promise<void>
}

const initialSite: string | null = (() => {
  try { return localStorage.getItem(STORAGE_KEY) } catch { return null }
})()

export const useSiteStore = create<SiteState>((set) => ({
  selectedSite: initialSite,
  sites: [],
  setSelectedSite: (id) => {
    try {
      if (id) localStorage.setItem(STORAGE_KEY, id)
      else localStorage.removeItem(STORAGE_KEY)
    } catch { /* ignore — selection still applies for the session */ }
    set({ selectedSite: id })
  },
  loadSites: async () => {
    try {
      const sites = await fetchSites()
      set({ sites })
      // Drop a stale persisted selection that no longer maps to a real site.
      const cur = useSiteStore.getState().selectedSite
      if (cur && !sites.some((s) => String(s.id) === cur)) {
        useSiteStore.getState().setSelectedSite(null)
      }
    } catch { /* ignore — selector falls back to "All Sites" */ }
  },
}))

/**
 * Convenience hook mirroring the original SiteContext API: the selected site id,
 * its resolved display name, a setter, and the site list. Backed by the Zustand
 * store so the selection is shared across every page and persisted to
 * localStorage.
 */
export function useSite(): {
  selectedSite: string | null
  selectedSiteName: string
  setSelectedSite: (id: string | null) => void
  sites: Site[]
} {
  const selectedSite = useSiteStore((s) => s.selectedSite)
  const sites = useSiteStore((s) => s.sites)
  const setSelectedSite = useSiteStore((s) => s.setSelectedSite)
  const selectedSiteName =
    sites.find((s) => String(s.id) === selectedSite)?.name ?? 'All Sites'
  return { selectedSite, selectedSiteName, setSelectedSite, sites }
}
