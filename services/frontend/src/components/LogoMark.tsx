/**
 * spane logo mark — a "single pane of glass" icon: a rounded pane framing a
 * window cross with a focus node at the intersection (unified infrastructure
 * visibility). The tile colour follows `currentColor` (set via text-* utility)
 * so it adapts to brand/theme; the glyph stays white on the coloured tile.
 *
 * The standalone SVG assets in /public (logo.svg, logo-icon.svg, favicon.svg)
 * mirror this design for the browser tab and external use.
 */
export default function LogoMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 40 40"
      className={className}
      role="img"
      aria-label="spane"
      xmlns="http://www.w3.org/2000/svg"
    >
      <rect width="40" height="40" rx="9" fill="currentColor" />
      <g fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
        <rect x="10" y="10" width="20" height="20" rx="4" />
        <path d="M10 18h20M18 18v12" />
      </g>
      <circle cx="18" cy="18" r="2.2" fill="#fff" />
    </svg>
  )
}
