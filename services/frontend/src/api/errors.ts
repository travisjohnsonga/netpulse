/**
 * Turn an Axios/API error into a human-readable message, preferring the
 * server's actual validation detail over a generic failure string.
 *
 * Handles the shapes DRF returns:
 *   - plain string body
 *   - {detail: "..."} / {error: "..."} / {message: "..."}
 *   - field errors: {email: ["Enter a valid email address."], ...}
 *     → "Email: Enter a valid email address."
 *   - {non_field_errors: [...]} → message with no field prefix
 *
 * Falls back to `fallback` when nothing usable is present.
 */
export function parseApiErrors(error: unknown, fallback = 'An unexpected error occurred'): string {
  const data = (error as { response?: { data?: unknown } })?.response?.data
  if (data == null) return fallback
  if (typeof data === 'string') return data || fallback

  if (typeof data === 'object') {
    const d = data as Record<string, unknown>
    if (typeof d.detail === 'string') return d.detail
    if (typeof d.error === 'string') return d.error
    if (typeof d.message === 'string') return d.message

    const lines = Object.entries(d)
      .map(([field, errs]) => {
        const label = field.replace(/_/g, ' ')
        const prefix = field === 'non_field_errors' ? '' : label.charAt(0).toUpperCase() + label.slice(1) + ': '
        const msg = Array.isArray(errs)
          ? errs.map((e) => (typeof e === 'string' ? e : JSON.stringify(e))).join(', ')
          : typeof errs === 'string'
            ? errs
            : JSON.stringify(errs)
        return `${prefix}${msg}`.trim()
      })
      .filter(Boolean)
    if (lines.length) return lines.join('\n')
  }

  return fallback
}
