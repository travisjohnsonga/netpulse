import clsx from 'clsx'

// Network-device platform → vendor key. The vendor key maps 1:1 to an SVG in
// /public/vendor-icons/<vendor>.svg (served at /vendor-icons/<vendor>.svg).
// The icons are simple, original marks (lettermark tiles / generic shapes) —
// not reproductions of trademarked vendor logos.
const PLATFORM_VENDOR: Record<string, string> = {
  // Cisco
  ios: 'cisco', ios_xe: 'cisco', ios_xr: 'cisco', nxos: 'cisco',
  // Juniper / Mist
  junos: 'juniper', mist_ap: 'juniper', mist_sw: 'juniper', mist_gw: 'juniper',
  // HPE / Aruba
  aos_cx: 'aruba', aruba: 'aruba', aos_s: 'aruba', instant_on: 'aruba',
  // Fortinet
  fortios: 'fortinet',
  // Palo Alto
  panos: 'paloalto',
  // SonicWall
  sonicwall: 'sonicwall',
  // Ubiquiti / UniFi
  unifi_ap: 'ubiquiti', unifi_sw: 'ubiquiti', unifi_gw: 'ubiquiti',
  unifi_udm: 'ubiquiti', unifi_uckp: 'ubiquiti', unifi_ucg: 'ubiquiti',
  // Servers (spane agent)
  linux: 'linux', windows: 'windows',
}

// Vendor keys that have an icon file. Anything else falls back to 'unknown'.
const VENDOR_ICONS = new Set([
  'cisco', 'juniper', 'aruba', 'fortinet', 'paloalto', 'sonicwall',
  'ubiquiti', 'linux', 'windows', 'unknown',
])

// Accept coarse/alternate vendor keys (e.g. the Wireless page's source field, or
// a manufacturer string) and normalise them to an icon key.
const VENDOR_ALIAS: Record<string, string> = {
  unifi: 'ubiquiti', ubiquiti: 'ubiquiti',
  mist: 'juniper', juniper: 'juniper',
  hpe: 'aruba', hp: 'aruba', aruba: 'aruba',
  'palo alto': 'paloalto', paloalto: 'paloalto', 'palo-alto': 'paloalto',
  cisco: 'cisco', fortinet: 'fortinet', sonicwall: 'sonicwall',
  linux: 'linux', windows: 'windows', microsoft: 'windows',
}

const VENDOR_LABEL: Record<string, string> = {
  cisco: 'Cisco', juniper: 'Juniper', aruba: 'Aruba', fortinet: 'Fortinet',
  paloalto: 'Palo Alto', sonicwall: 'SonicWall', ubiquiti: 'Ubiquiti',
  linux: 'Linux', windows: 'Windows', unknown: 'Unknown',
}

/** Resolve a platform (and/or an explicit vendor hint) to an icon vendor key. */
export function resolveVendor(platform?: string | null, vendor?: string | null): string {
  if (vendor) {
    const key = vendor.toLowerCase().trim()
    const aliased = VENDOR_ALIAS[key] ?? key
    if (VENDOR_ICONS.has(aliased)) return aliased
  }
  return PLATFORM_VENDOR[(platform || '').toLowerCase().trim()] ?? 'unknown'
}

export function vendorLabel(platform?: string | null, vendor?: string | null): string {
  return VENDOR_LABEL[resolveVendor(platform, vendor)] ?? 'Unknown'
}

interface VendorLogoProps {
  /** Device platform (e.g. ios_xe, mist_ap, unifi_udm). */
  platform?: string | null
  /** Optional explicit vendor key/manufacturer; wins over platform when known. */
  vendor?: string | null
  /** Pixel size of the square icon (default 24). */
  size?: number
  className?: string
  /** Show the vendor name beside the icon. */
  showName?: boolean
}

export default function VendorLogo({ platform, vendor, size = 24, className, showName = false }: VendorLogoProps) {
  const key = resolveVendor(platform, vendor)
  const label = VENDOR_LABEL[key] ?? 'Unknown'
  return (
    <span className={clsx('inline-flex items-center gap-1.5 align-middle', className)}>
      <img
        src={`/vendor-icons/${key}.svg`}
        alt={label}
        title={label}
        width={size}
        height={size}
        className="shrink-0 rounded"
        loading="lazy"
      />
      {showName && <span className="text-sm text-gray-700 dark:text-gray-200">{label}</span>}
    </span>
  )
}
