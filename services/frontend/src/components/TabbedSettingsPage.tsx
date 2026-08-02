import { type ReactNode } from 'react'
import { Tabs } from '../pages/Settings'
import { useTabParam } from '../lib/useTabParam'

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
  // Shared hook: active tab in ?tab=…, restored on refresh, default tab omits
  // the param (clean URL), invalid param falls back to the first tab.
  const [active, setActive] = useTabParam(tabs.map((t) => t.id), tabs[0].id)

  return (
    <div>
      <Tabs tabs={tabs.map(({ id, label }) => ({ id, label }))} active={active} onChange={setActive} />
      {tabs.find((t) => t.id === active)?.element}
    </div>
  )
}
