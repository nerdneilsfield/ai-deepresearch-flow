import { buildUrl, fetchJson } from '@/lib/http'
import {
  createPaperDetailFreshness,
  equalPaperDetailFreshness,
  readPaperContentRecord,
  touchPaperContentRecord,
  writePaperContentRecord,
} from '@/lib/paper-content-cache'
import {
  FacetResponseSchema,
  FacetStatsResponseSchema,
  ManifestSchema,
  PaperBibtexSchema,
  PaperDetailSchema,
  SearchResponseSchema,
  StatsResponseSchema,
} from '@/types/api'
import type {
  FacetResponse,
  FacetStatsResponse,
  Manifest,
  PaperBibtex,
  PaperDetail,
  SearchResponse,
  StatsResponse,
} from '@/types/api'

export async function searchPapers(
  query: string,
  page: number,
  pageSize: number,
  sort?: string,
  signal?: AbortSignal
) {
  const url = buildUrl('/search', { q: query, page, page_size: pageSize, sort })
  const data = await fetchJson(url, { signal })
  return SearchResponseSchema.parse(data)
}

export async function listPapers(page: number, pageSize: number, sort?: string, signal?: AbortSignal) {
  const url = buildUrl('/search', { page, page_size: pageSize, sort })
  const data = await fetchJson(url, { signal })
  return SearchResponseSchema.parse(data)
}

export async function getPaperDetail(paperId: string): Promise<PaperDetail> {
  const url = buildUrl(`/papers/${paperId}`)
  const data = await fetchJson(url)
  return PaperDetailSchema.parse(data)
}

export interface CachedPaperDetailOptions {
  onRevalidated?: (detail: PaperDetail) => void
}

export async function getPaperDetailCached(
  paperId: string,
  options: CachedPaperDetailOptions = {},
): Promise<PaperDetail> {
  const cachedRecord = await readPaperContentRecord(paperId)
  if (cachedRecord?.detail && cachedRecord.detailFreshness) {
    await touchPaperContentRecord(paperId)

    void getPaperDetail(paperId)
      .then(async (freshDetail) => {
        const latestRecord = await readPaperContentRecord(paperId)
        if (!latestRecord) return
        const nextFreshness = createPaperDetailFreshness(freshDetail)
        if (equalPaperDetailFreshness(latestRecord.detailFreshness, nextFreshness)) {
          return
        }
        await writePaperContentRecord({
          paperId,
          detail: freshDetail,
          detailFreshness: nextFreshness,
          summaries: latestRecord.summaries,
          translations: latestRecord.translations,
          lastAccessedAt: latestRecord.lastAccessedAt,
        })
        options.onRevalidated?.(freshDetail)
      })
      .catch(() => {
        // Ignore background refresh failures and keep serving the cached detail.
      })

    return cachedRecord.detail
  }

  const freshDetail = await getPaperDetail(paperId)
  await writePaperContentRecord({
    paperId,
    detail: freshDetail,
    detailFreshness: createPaperDetailFreshness(freshDetail),
    summaries: cachedRecord?.summaries ?? {},
    translations: cachedRecord?.translations ?? {},
  })
  return freshDetail
}

export async function getFacet(facet: string, page: number, pageSize: number): Promise<FacetResponse> {
  const url = buildUrl(`/facets/${facet}`, { page, page_size: pageSize })
  const data = await fetchJson(url)
  return FacetResponseSchema.parse(data)
}

export async function getFacetPapers(
  facet: string,
  facetId: string | number,
  page: number,
  pageSize: number
): Promise<SearchResponse> {
  const url = buildUrl(`/facets/${facet}/${facetId}/papers`, { page, page_size: pageSize })
  const data = await fetchJson(url)
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
  const data = await fetchJson(url)
  return SearchResponseSchema.parse(data)
}

export async function getFacetByValueStats(
  facet: string,
  value: string
): Promise<FacetStatsResponse> {
  const encoded = encodeURIComponent(value)
  const url = buildUrl(`/facets/${facet}/by-value/${encoded}/stats`)
  const data = await fetchJson(url)
  return FacetStatsResponseSchema.parse(data)
}

export async function getStats(): Promise<StatsResponse> {
  const url = buildUrl('/stats')
  const data = await fetchJson(url)
  return StatsResponseSchema.parse(data)
}

export async function getPaperBibtex(paperId: string): Promise<PaperBibtex> {
  const url = buildUrl(`/papers/${paperId}/bibtex`)
  const data = await fetchJson(url)
  return PaperBibtexSchema.parse(data)
}

export async function fetchManifest(url: string): Promise<Manifest> {
  const data = await fetchJson(url)
  return ManifestSchema.parse(data)
}

export interface BibtexMatchedItem {
  bibtex_key: string
  paper_id: string
  match_method: 'doi' | 'title'
  title: string
  year: string | null
  venue: string | null
  authors: string[]
}

export interface BibtexUnmatchedItem {
  bibtex_key: string
  title: string | null
  search_query: string
}

export interface BibtexMatchResult {
  matched: BibtexMatchedItem[]
  unmatched: BibtexUnmatchedItem[]
  stats: { total: number; matched: number; unmatched: number }
}

export async function matchBibtex(bibtexRaw: string): Promise<BibtexMatchResult> {
  const url = buildUrl('/papers/match-bibtex')
  const data = await fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bibtex_raw: bibtexRaw }),
  })
  return data as BibtexMatchResult
}

export type {
  FacetResponse,
  FacetStatsResponse,
  Manifest,
  PaperBibtex,
  PaperDetail,
  SearchResponse,
  StatsResponse,
} from '@/types/api'

export {
  advancedSearch,
  verifyToken,
  AdvancedSearchHTTPError,
} from '@/lib/advanced-search'
export { buildUrl, fetchJson, fetchResponse, fetchText } from '@/lib/http'
export type {
  AdvancedSearchFilters,
  AdvancedSearchParams,
  AdvancedSearchResponse,
  AdvancedSearchResult,
  VerifyResult,
} from '@/lib/advanced-search'
