// Shared data-table styling so the Services, Disk, Network, and role-check
// detail tables read consistently. Zebra stripes (subtle) replace row dividers;
// hover stays distinguishable over the stripe. Apply STRIPED_ROW to each <tr>.
//
// Rollout: applied to the Services table now; the Disk/Network/role-check tables
// can adopt STRIPED_ROW (drop their `border-t …` dividers) as a follow-up
// without re-deriving the colors.
export const STRIPED_ROW =
  'even:bg-gray-50/70 dark:even:bg-gray-700/20 hover:bg-blue-50 dark:hover:bg-gray-700/40'

// Content-width data table: columns size to content, slack pools on the right
// (no w-full stretch). Pair with whitespace-nowrap cells to keep columns tight.
export const CONTENT_TABLE = 'text-sm'

// Frozen first column for laptop-width horizontal scroll: the identity column
// (Hostname) stays anchored while the metric columns scroll under it. Solid bg
// (opaque) so scrolled cells don't show through; z above the body. Pair with a
// whitespace-nowrap body so columns keep their natural width and the table
// scrolls (in an overflow-x-auto wrapper) rather than squishing.
export const STICKY_COL = 'sticky left-0 z-10 bg-white dark:bg-gray-800'
export const STICKY_COL_HEAD = 'sticky left-0 z-20 bg-white dark:bg-gray-800'
