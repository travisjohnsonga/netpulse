import { create } from 'zustand'

export type TempUnit = 'C' | 'F'

const STORAGE_KEY = 'netpulse.temp_unit'

interface UnitsState {
  unit: TempUnit
  /** Set the display unit and persist to localStorage immediately. */
  setUnit: (u: TempUnit) => void
  /** Reconcile from backend preferences without changing the source of truth. */
  syncFromServer: (u: TempUnit) => void
}

const initial: TempUnit = (() => {
  try {
    return (localStorage.getItem(STORAGE_KEY) as TempUnit) === 'F' ? 'F' : 'C'
  } catch {
    return 'C'
  }
})()

export const useUnitsStore = create<UnitsState>((set, get) => ({
  unit: initial,
  setUnit: (u) => {
    try { localStorage.setItem(STORAGE_KEY, u) } catch { /* ignore */ }
    set({ unit: u })
  },
  syncFromServer: (u) => {
    if (u === get().unit) return
    try { localStorage.setItem(STORAGE_KEY, u) } catch { /* ignore */ }
    set({ unit: u })
  },
}))
