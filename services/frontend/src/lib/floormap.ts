import { useEffect, useRef, useState } from 'react'

/** Track an element's content-box size (via ResizeObserver). */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T | null>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setSize({ width: el.clientWidth, height: el.clientHeight })
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

/**
 * The rectangle where a (W×H) image renders inside a (cw×ch) box under
 * object-contain: uniformly scaled to fit, then centred (letterboxed). Floor-plan
 * markers must be positioned within THIS rectangle — not the full container — so
 * marker space equals image space 1:1.
 */
export function containedRect(W: number, H: number, cw: number, ch: number) {
  if (W <= 0 || H <= 0 || cw <= 0 || ch <= 0) return { x: 0, y: 0, w: cw, h: ch }
  const scale = Math.min(cw / W, ch / H)
  const w = W * scale
  const h = H * scale
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h }
}
