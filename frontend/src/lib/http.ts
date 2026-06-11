import { API_BASE, SEARCH_TIMEOUT_MS } from '@/lib/config'

async function sleep(ms: number, signal?: AbortSignal) {
  if (signal?.aborted) {
    throw signal.reason ?? new DOMException('Aborted', 'AbortError')
  }
  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => {
      signal?.removeEventListener('abort', abort)
      resolve()
    }, ms)
    const abort = () => {
      clearTimeout(timeout)
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'))
    }
    signal?.addEventListener('abort', abort, { once: true })
  })
}

export type FetchOptions = RequestInit & { timeoutMs?: number; retry?: number }

export async function fetchResponse(
  url: string,
  options: FetchOptions = {}
): Promise<Response> {
  const { timeoutMs = SEARCH_TIMEOUT_MS, retry = 2, signal, ...rest } = options
  let attempt = 0
  let lastError: unknown

  while (attempt <= retry) {
    const controller = new AbortController()
    const abortFromCaller = () => controller.abort(signal?.reason)
    if (signal?.aborted) controller.abort(signal.reason)
    signal?.addEventListener('abort', abortFromCaller, { once: true })
    let timeoutAborted = false
    const timeout = setTimeout(() => {
      timeoutAborted = true
      controller.abort(new DOMException('Request timed out', 'TimeoutError'))
    }, timeoutMs)
    try {
      const response = await fetch(url, { ...rest, signal: controller.signal })
      clearTimeout(timeout)
      signal?.removeEventListener('abort', abortFromCaller)
      if (!response.ok && response.status >= 500 && response.status < 600 && attempt < retry) {
        attempt += 1
        await sleep(300 * Math.pow(2, attempt), signal ?? undefined)
        continue
      }
      return response
    } catch (err) {
      clearTimeout(timeout)
      signal?.removeEventListener('abort', abortFromCaller)
      lastError = err
      if (
        signal?.aborted ||
        (!timeoutAborted && err instanceof DOMException && err.name === 'AbortError') ||
        attempt >= retry
      ) {
        throw err
      }
      attempt += 1
      await sleep(300 * Math.pow(2, attempt), signal ?? undefined)
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
