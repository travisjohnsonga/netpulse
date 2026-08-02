import { useSearchParams } from 'react-router-dom'

/**
 * Persist the active tab (or any single-choice view, e.g. a table/tree toggle)
 * in a URL query param so a refresh restores it and the URL is shareable.
 *
 * One correct implementation shared by every tabbed page (ServerDetail,
 * DeviceDetail, SiteDetail, the settings tabs, the Sites view toggle):
 *  - reading restores the value on refresh;
 *  - the DEFAULT value omits the param entirely (clean URL — no `?tab=Overview`);
 *  - an invalid/unknown param falls back to the default;
 *  - writes use `replace` so tab clicks don't spam browser history.
 *
 * @param tabs       the allowed values (readonly list of ids)
 * @param defaultTab the value used when the param is absent or invalid
 * @param paramName  the query-param name (default "tab"; e.g. "view" for a toggle)
 */
export function useTabParam<T extends string>(
  tabs: readonly T[],
  defaultTab: T,
  paramName = 'tab',
): [T, (next: T) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get(paramName)
  const value = (raw && (tabs as readonly string[]).includes(raw) ? raw : defaultTab) as T

  const setValue = (next: T) => {
    setParams(
      (prev) => {
        const sp = new URLSearchParams(prev)
        if (next === defaultTab) sp.delete(paramName)
        else sp.set(paramName, next)
        return sp
      },
      { replace: true },
    )
  }

  return [value, setValue]
}
