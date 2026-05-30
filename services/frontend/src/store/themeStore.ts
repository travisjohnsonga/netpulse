import { create } from 'zustand'

export type Theme = 'light' | 'dark' | 'system'

const STORAGE_KEY = 'netpulse.theme'

function systemPrefersDark(): boolean {
  return window.matchMedia('(prefers-color-scheme: dark)').matches
}

function resolve(theme: Theme): boolean {
  return theme === 'dark' || (theme === 'system' && systemPrefersDark())
}

/** Add/remove the `dark` class on <html> to match the resolved theme. */
function apply(theme: Theme): void {
  document.documentElement.classList.toggle('dark', resolve(theme))
}

interface ThemeState {
  theme: Theme
  /** Set the theme, persist to localStorage, and apply immediately. */
  setTheme: (t: Theme) => void
  /** Quick light/dark flip (collapses 'system' to an explicit choice). */
  toggle: () => void
  /** Sync from backend preferences without re-persisting elsewhere. */
  syncFromServer: (t: Theme) => void
}

const initial: Theme = (() => {
  try { return (localStorage.getItem(STORAGE_KEY) as Theme) || 'system' } catch { return 'system' }
})()

apply(initial)

// Keep `system` in step with OS changes while that mode is active.
try {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (useThemeStore.getState().theme === 'system') apply('system')
  })
} catch { /* ignore */ }

export const useThemeStore = create<ThemeState>((set, get) => ({
  theme: initial,
  setTheme: (t) => {
    try { localStorage.setItem(STORAGE_KEY, t) } catch { /* ignore */ }
    apply(t)
    set({ theme: t })
  },
  toggle: () => {
    const isDark = document.documentElement.classList.contains('dark')
    get().setTheme(isDark ? 'light' : 'dark')
  },
  syncFromServer: (t) => {
    if (t === get().theme) return
    try { localStorage.setItem(STORAGE_KEY, t) } catch { /* ignore */ }
    apply(t)
    set({ theme: t })
  },
}))

export function isDark(): boolean {
  return document.documentElement.classList.contains('dark')
}
