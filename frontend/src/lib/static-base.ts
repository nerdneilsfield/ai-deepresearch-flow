import { STATIC_BASE } from './config'

const MARKERS = ['/manifest/', '/summary/', '/pdf/', '/md/', '/md_translate/', '/images/']

export function staticBaseFromUrl(url?: string | null) {
  if (!url) return ''
  const trimmed = url.replace(/\?.*$/, '').replace(/#.*$/, '')
  for (const marker of MARKERS) {
    const idx = trimmed.indexOf(marker)
    if (idx !== -1) return trimmed.slice(0, idx)
  }
  return trimmed.replace(/\/$/, '')
}

export function resolveStaticBaseUrl(...candidates: Array<string | null | undefined>) {
  for (const candidate of candidates) {
    if (!candidate) continue
    const base = staticBaseFromUrl(candidate)
    if (base) return base
    if (candidate.startsWith('/') && STATIC_BASE) return STATIC_BASE
  }
  if (STATIC_BASE) return STATIC_BASE
  if (typeof window !== 'undefined') {
    return window.location.origin
  }
  return ''
}
