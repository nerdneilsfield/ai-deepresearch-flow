import { buildUrl, fetchJson, fetchText } from '@/lib/http'
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
  BibtexMatchResultSchema,
  ManifestSchema,
  PaperBibtexSchema,
  PaperDetailSchema,
  SearchResponseSchema,
  StatsResponseSchema,
} from '@/types/api'
import type {
  FacetResponse,
  FacetStatsResponse,
  BibtexMatchResult,
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

export async function getSummaryPayloadCached(
  paperId: string,
  template: string,
  url: string,
): Promise<Record<string, unknown>> {
  const cachedRecord = await readPaperContentRecord(paperId)
  const cachedSummary = cachedRecord?.summaries?.[template]
  if (cachedSummary && cachedSummary.url === url) {
    await touchPaperContentRecord(paperId)
    return JSON.parse(JSON.stringify(cachedSummary.payload))
  }

  const payload = (await fetchJson(url)) as Record<string, unknown>
  const accessedAt = Date.now()
  await writePaperContentRecord({
    paperId,
    detail: cachedRecord?.detail ?? null,
    detailFreshness: cachedRecord?.detailFreshness ?? null,
    summaries: {
      ...(cachedRecord?.summaries ?? {}),
      [template]: {
        url,
        payload: JSON.parse(JSON.stringify(payload)),
        cachedAt: Date.now(),
      },
    },
    translations: cachedRecord?.translations ?? {},
    lastAccessedAt: Math.max(accessedAt, (cachedRecord?.lastAccessedAt ?? 0) + 1),
  })
  return payload
}

export async function getTranslatedMarkdownCached(
  paperId: string,
  lang: string,
  url: string,
): Promise<string> {
  const cachedRecord = await readPaperContentRecord(paperId)
  const cachedTranslation = cachedRecord?.translations?.[lang]
  if (cachedTranslation && cachedTranslation.url === url) {
    await touchPaperContentRecord(paperId)
    return cachedTranslation.markdown
  }

  const markdown = await fetchText(url)
  const accessedAt = Date.now()
  await writePaperContentRecord({
    paperId,
    detail: cachedRecord?.detail ?? null,
    detailFreshness: cachedRecord?.detailFreshness ?? null,
    summaries: cachedRecord?.summaries ?? {},
    translations: {
      ...(cachedRecord?.translations ?? {}),
      [lang]: {
        url,
        markdown,
        cachedAt: Date.now(),
      },
    },
    lastAccessedAt: Math.max(accessedAt, (cachedRecord?.lastAccessedAt ?? 0) + 1),
  })
  return markdown
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
  pageSize: number,
  signal?: AbortSignal
): Promise<SearchResponse> {
  const url = buildUrl(`/facets/${facet}/${facetId}/papers`, { page, page_size: pageSize })
  const data = await fetchJson(url, { signal })
  return SearchResponseSchema.parse(data)
}

export async function getFacetByValuePapers(
  facet: string,
  value: string,
  page: number,
  pageSize: number,
  signal?: AbortSignal
): Promise<SearchResponse> {
  const encoded = encodeURIComponent(value)
  const url = buildUrl(`/facets/${facet}/by-value/${encoded}/papers`, { page, page_size: pageSize })
  const data = await fetchJson(url, { signal })
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

export async function matchBibtex(bibtexRaw: string): Promise<BibtexMatchResult> {
  const url = buildUrl('/papers/match-bibtex')
  const data = await fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bibtex_raw: bibtexRaw }),
  })
  return BibtexMatchResultSchema.parse(data)
}

export type {
  FacetResponse,
  FacetStatsResponse,
  BibtexMatchResult,
  BibtexMatchedItem,
  BibtexUnmatchedItem,
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
