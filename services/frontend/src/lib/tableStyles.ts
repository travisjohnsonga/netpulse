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
