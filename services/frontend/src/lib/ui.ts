// Shared page-chrome primitives so every page reads as one product, not a set of
// independently-styled screens. These are deliberately plain class-string
// constants (not components) so a page composes them inline and Travis can tweak
// a single value here to shift the whole app. Pair with:
//   - tableStyles.ts  (STRIPED_ROW / CONTENT_TABLE) for data tables
//   - StatCard.tsx     for summary stat cards
//   - EmptyState.tsx   for empty states (text-first, no emoji)
//
// Intentionally NOT unified: the actual DATA each page shows (servers: agent
// CPU/mem/disk; devices: ping/SNMP/reachability). Unify the frame, not the content.

// ── Text color tokens (dark-mode readable) ─────────────────────────────────────
// A 3-level hierarchy. The dark shades are deliberately ONE step lighter than the
// old conventions (secondary gray-400 → gray-300, muted gray-500 → gray-400) so
// secondary/muted text clears ~4.5:1 on the dark gray-800/900 bg instead of the
// old dim ~3:1. Use these for body/label/timestamp text across pages.
export const TEXT_PRIMARY = 'text-gray-900 dark:text-gray-100'      // headings, key values
export const TEXT_SECONDARY = 'text-gray-600 dark:text-gray-300'    // body, labels, cell values
export const TEXT_MUTED = 'text-gray-500 dark:text-gray-400'        // timestamps, hints, "—"

// ── Page header ───────────────────────────────────────────────────────────────
export const PAGE_TITLE = 'text-2xl font-bold text-gray-900 dark:text-gray-100'
export const PAGE_SUBTITLE = `text-sm ${TEXT_MUTED} mt-0.5`

// ── Filter bar (search input + dropdowns), consistent across list pages ────────
export const INPUT =
  'px-3 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-600 ' +
  'bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 ' +
  'placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-blue-500/40 focus:border-blue-500'
export const SELECT = INPUT + ' cursor-pointer'

// ── Buttons ────────────────────────────────────────────────────────────────────
export const BTN_PRIMARY =
  'px-4 py-2 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-700 text-white ' +
  'transition-colors disabled:opacity-40 disabled:cursor-not-allowed'
export const BTN_SECONDARY =
  'px-4 py-2 rounded-lg text-sm font-medium border border-gray-300 dark:border-gray-600 ' +
  'text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors'

// ── Surfaces ───────────────────────────────────────────────────────────────────
export const CARD =
  'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-xl'

// ── Status badge tones (shared vocabulary for up/down/warn/neutral) ────────────
export type BadgeTone = 'ok' | 'down' | 'warn' | 'neutral'
export const BADGE_TONE: Record<BadgeTone, string> = {
  ok: 'text-green-700 bg-green-100 dark:text-green-300 dark:bg-green-900/40',
  down: 'text-red-700 bg-red-100 dark:text-red-300 dark:bg-red-900/40',
  warn: 'text-amber-700 bg-amber-100 dark:text-amber-300 dark:bg-amber-900/40',
  neutral: 'text-gray-600 bg-gray-100 dark:text-gray-400 dark:bg-gray-700/50',
}
export const badge = (tone: BadgeTone) =>
  `px-2 py-0.5 rounded-full text-xs font-medium ${BADGE_TONE[tone]}`
