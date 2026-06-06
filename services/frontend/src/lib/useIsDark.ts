import { useEffect, useState } from 'react'

// True while the app is in dark mode (the `dark` class on <html>, toggled by the
// theme store). ECharts options can't read Tailwind `dark:` classes, so charts
// use this to pick readable label/axis colors. Watching the class directly (vs.
// the theme store) also catches OS-preference flips while in 'system' mode.
function darkNow(): boolean {
  return typeof document !== 'undefined' && document.documentElement.classList.contains('dark')
}

export function useIsDark(): boolean {
  const [isDark, setIsDark] = useState(darkNow)
  useEffect(() => {
    const el = document.documentElement
    const observer = new MutationObserver(() => setIsDark(el.classList.contains('dark')))
    observer.observe(el, { attributes: true, attributeFilter: ['class'] })
    setIsDark(el.classList.contains('dark')) // sync in case it changed before observe
    return () => observer.disconnect()
  }, [])
  return isDark
}

// Shared chart text colors — primary labels and muted axis ticks per theme.
export function chartColors(isDark: boolean) {
  return {
    text: isDark ? '#e2e8f0' : '#1e293b', // slate-200 / slate-800
    muted: isDark ? '#94a3b8' : '#64748b', // slate-400 / slate-500
    split: isDark ? 'rgba(148,163,184,0.15)' : 'rgba(100,116,139,0.15)',
  }
}
