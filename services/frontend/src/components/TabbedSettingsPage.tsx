import { type ReactNode } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Tabs } from '../pages/Settings'

export interface SettingsTab {
  id: string
  label: string
  element: ReactNode
}

/**
 * Wraps several existing settings sections as tabs under one route. The active
 * tab lives in the URL (?tab=…) so links and back/forward work; defaults to the
 * first tab when the param is missing or unknown.
 */
export default function TabbedSettingsPage({ tabs }: { tabs: SettingsTab[] }) {
  const [params, setParams] = useSearchParams()
  const requested = params.get('tab')
  const active = tabs.some((t) => t.id === requested) ? requested! : tabs[0].id

  const setActive = (id: string) => {
    const next = new URLSearchParams(params)
    next.set('tab', id)
    setParams(next, { replace: true })
  }

  return (
    <div>
      <Tabs tabs={tabs.map(({ id, label }) => ({ id, label }))} active={active} onChange={setActive} />
      {tabs.find((t) => t.id === active)?.element}
    </div>
  )
}
