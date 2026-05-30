import { create } from 'zustand'
import { fetchPreferences, type UserPreferences } from '../api/client'
import { useThemeStore } from './themeStore'

interface PrefsState {
  prefs: UserPreferences | null
  loaded: boolean
  /** Fetch preferences from the API and sync the theme to the backend value. */
  load: () => Promise<void>
  /** Update the cached prefs (after a save) and re-sync theme. */
  set: (p: UserPreferences) => void
}

export const usePreferencesStore = create<PrefsState>((set) => ({
  prefs: null,
  loaded: false,
  load: async () => {
    try {
      const prefs = await fetchPreferences()
      useThemeStore.getState().syncFromServer(prefs.theme)
      set({ prefs, loaded: true })
    } catch {
      set({ loaded: true })
    }
  },
  set: (prefs) => {
    useThemeStore.getState().syncFromServer(prefs.theme)
    set({ prefs })
  },
}))
