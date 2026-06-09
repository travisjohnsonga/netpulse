// LLDP capability tokens → display icon + friendly label. Shared by the LLDP
// "Not in Inventory" page and the LLDP settings page so both render the same
// names/icons. Tokens mirror apps.devices.lldp.normalize_capabilities output.
export const CAP_META: Record<string, { icon: string; label: string }> = {
  router: { icon: '🔁', label: 'Router' },
  bridge: { icon: '🔀', label: 'Switch/Bridge' },
  'wlan-ap': { icon: '📶', label: 'Wireless AP' },
  telephone: { icon: '☎️', label: 'IP Phone' },
  station: { icon: '💻', label: 'Workstation/PC' },
  repeater: { icon: '📍', label: 'Repeater/Hub' },
  docsis: { icon: '📡', label: 'Cable/DOCSIS' },
  other: { icon: '•', label: 'Other' },
}

// Capability tokens in display order (matches the backend KNOWN_CAPABILITIES).
export const CAP_OPTIONS = ['router', 'bridge', 'wlan-ap', 'telephone', 'station',
  'repeater', 'docsis', 'other'] as const

export function capLabel(token: string): string {
  return CAP_META[token]?.label ?? token
}
