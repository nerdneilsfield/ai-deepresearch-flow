import { API_BASE, SEARCH_TIMEOUT_MS } from '@/lib/config'

async function sleep(ms: number) {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

export type FetchOptions = RequestInit & { timeoutMs?: number; retry?: number }

export async function fetchResponse(
  url: string,
  options: FetchOptions = {}
): Promise<Response> {
  const { timeoutMs = SEARCH_TIMEOUT_MS, retry = 2, ...rest } = options
  let attempt = 0
  let lastError: unknown

  while (attempt <= retry) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const response = await fetch(url, { ...rest, signal: controller.signal })
      clearTimeout(timeout)
      if (!response.ok && response.status >= 500 && response.status < 600 && attempt < retry) {
        attempt += 1
        await sleep(300 * Math.pow(2, attempt))
        continue
      }
      return response
    } catch (err) {
      clearTimeout(timeout)
      lastError = err
      if (attempt >= retry) {
        throw err
      }
      attempt += 1
      await sleep(300 * Math.pow(2, attempt))
    }
  }

  throw lastError
}

export async function fetchJson(
  url: string,
  options: FetchOptions = {}
): Promise<unknown> {
  const response = await fetchResponse(url, options)
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || response.statusText)
  }
  return response.json()
}

export async function fetchText(
  url: string,
  options: FetchOptions = {}
): Promise<string> {
  const response = await fetchResponse(url, options)
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || response.statusText)
  }
  return response.text()
}

export function buildUrl(path: string, params?: Record<string, string | number | undefined | null>) {
  const base = API_BASE.replace(/\/+$/, '')
  const absolute = base.startsWith('http://') || base.startsWith('https://')
  const url = absolute
    ? new URL(`${base}${path}`)
    : new URL(`${base}${path}`.replace(/\/+$/, ''), window.location.origin)
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === '') continue
      url.searchParams.set(key, String(value))
    }
  }
  return absolute ? url.toString() : url.pathname + url.search
}
