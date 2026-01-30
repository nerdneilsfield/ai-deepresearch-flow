import { API_BASE, SEARCH_TIMEOUT_MS } from '@/lib/config'
import {
  FacetResponseSchema,
  FacetStatsResponseSchema,
  ManifestSchema,
  PaperDetailSchema,
  SearchResponseSchema,
  StatsResponseSchema,
} from '@/types/api'
import type {
  FacetResponse,
  FacetStatsResponse,
  Manifest,
  PaperDetail,
  SearchResponse,
  StatsResponse,
} from '@/types/api'

async function sleep(ms: number) {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

export async function fetchJson<T>(
  url: string,
  options: RequestInit & { timeoutMs?: number; retry?: number } = {}
): Promise<T> {
  const { timeoutMs = SEARCH_TIMEOUT_MS, retry = 2, ...rest } = options
  let attempt = 0
  let lastError: unknown

  while (attempt <= retry) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const response = await fetch(url, { ...rest, signal: controller.signal })
      clearTimeout(timeout)
      if (!response.ok) {
        if (response.status >= 500 && response.status < 600 && attempt < retry) {
          attempt += 1
          await sleep(300 * Math.pow(2, attempt))
          continue
        }
        const message = await response.text()
        throw new Error(message || response.statusText)
      }
      return (await response.json()) as T
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

export async function fetchText(
  url: string,
  options: RequestInit & { timeoutMs?: number; retry?: number } = {}
): Promise<string> {
  const { timeoutMs = SEARCH_TIMEOUT_MS, retry = 2, ...rest } = options
  let attempt = 0
  let lastError: unknown

  while (attempt <= retry) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), timeoutMs)
    try {
      const response = await fetch(url, { ...rest, signal: controller.signal })
      clearTimeout(timeout)
      if (!response.ok) {
        if (response.status >= 500 && response.status < 600 && attempt < retry) {
          attempt += 1
          await sleep(300 * Math.pow(2, attempt))
          continue
        }
        const message = await response.text()
        throw new Error(message || response.statusText)
      }
      return await response.text()
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

export async function searchPapers(
  query: string,
  page: number,
  pageSize: number,
  sort?: string,
  signal?: AbortSignal
) {
  const url = buildUrl('/search', { q: query, page, page_size: pageSize, sort })
  const data = await fetchJson<unknown>(url, { signal })
  return SearchResponseSchema.parse(data)
}

export async function listPapers(page: number, pageSize: number, sort?: string, signal?: AbortSignal) {
  const url = buildUrl('/search', { page, page_size: pageSize, sort })
  const data = await fetchJson<unknown>(url, { signal })
  return SearchResponseSchema.parse(data)
}

export async function getPaperDetail(paperId: string): Promise<PaperDetail> {
  const url = buildUrl(`/papers/${paperId}`)
  const data = await fetchJson<unknown>(url)
  return PaperDetailSchema.parse(data)
}

export async function getFacet(facet: string, page: number, pageSize: number): Promise<FacetResponse> {
  const url = buildUrl(`/facets/${facet}`, { page, page_size: pageSize })
  const data = await fetchJson<unknown>(url)
  return FacetResponseSchema.parse(data)
}

export async function getFacetPapers(
  facet: string,
  facetId: string | number,
  page: number,
  pageSize: number
): Promise<SearchResponse> {
  const url = buildUrl(`/facets/${facet}/${facetId}/papers`, { page, page_size: pageSize })
  const data = await fetchJson<unknown>(url)
  return SearchResponseSchema.parse(data)
}

export async function getFacetByValuePapers(
  facet: string,
  value: string,
  page: number,
  pageSize: number
): Promise<SearchResponse> {
  const encoded = encodeURIComponent(value)
  const url = buildUrl(`/facets/${facet}/by-value/${encoded}/papers`, { page, page_size: pageSize })
  const data = await fetchJson<unknown>(url)
  return SearchResponseSchema.parse(data)
}

export async function getFacetByValueStats(
  facet: string,
  value: string
): Promise<FacetStatsResponse> {
  const encoded = encodeURIComponent(value)
  const url = buildUrl(`/facets/${facet}/by-value/${encoded}/stats`)
  const data = await fetchJson<unknown>(url)
  return FacetStatsResponseSchema.parse(data)
}

export async function getStats(): Promise<StatsResponse> {
  const url = buildUrl('/stats')
  const data = await fetchJson<unknown>(url)
  return StatsResponseSchema.parse(data)
}

export async function fetchManifest(url: string): Promise<Manifest> {
  const data = await fetchJson<unknown>(url)
  return ManifestSchema.parse(data)
}

export type {
  FacetResponse,
  FacetStatsResponse,
  Manifest,
  PaperDetail,
  SearchResponse,
  StatsResponse,
} from '@/types/api'
