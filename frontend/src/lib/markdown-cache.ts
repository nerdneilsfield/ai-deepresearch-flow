import { fetchText } from '@/lib/api'

const MAX_CACHE_SIZE = 50
export const markdownCache = new Map<string, string>()

export function cacheSet(key: string, value: string) {
  if (markdownCache.has(key)) markdownCache.delete(key)
  markdownCache.set(key, value)
  if (markdownCache.size > MAX_CACHE_SIZE) {
    const oldest = markdownCache.keys().next().value
    if (oldest) markdownCache.delete(oldest)
  }
}

export async function prefetchMarkdown(url: string) {
  if (markdownCache.has(url)) return
  cacheSet(url, await fetchText(url))
}
