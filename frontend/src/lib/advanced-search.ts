import { ADVANCED_SEARCH_TIMEOUT_MS } from '@/lib/config'
import { buildUrl, fetchResponse } from '@/lib/http'

export type VerifyResult =
  | { valid: true }
  | { valid: false; reason: 'missing' | 'invalid' }

export interface AdvancedSearchFilters {
  year?: string
  venues?: string[]
  authors?: string[]
  keywords?: string[]
  tags?: string[]
  lang?: string
}

export interface AdvancedSearchParams {
  q: string
  topN?: number
  filters?: AdvancedSearchFilters
  mmrLambda?: number
  rerank?: 'auto' | 'always' | 'never'
}

export interface AdvancedSearchResult {
  chunk_id: string
  paper_id: string
  paper: {
    title: string
    authors: string[]
    year: string
    venue: string
    doi: string
    source_hash: string
  }
  chunk: {
    text: string
    field_name: string
    template_tag: string
    chunk_type: string
    chunk_index: number
    lang: string
  }
  scores: {
    dense?: number
    sparse?: number
    fused: number
    reranker?: number
    final: number
  }
}

export interface AdvancedSearchResponse {
  success: true
  trace_id: string
  query: {
    raw: string
    normalized: string
    applied_filters: Record<string, unknown>
  }
  results: AdvancedSearchResult[]
  metadata: {
    counts: Record<string, number>
    fusion: string
    reranker: { applied: boolean; model: string | null }
    mmr: { applied: boolean; lambda: number }
    embedding: { model: string; dimensions: number }
    latency_ms: Record<string, number>
  }
  degraded: boolean
  degradation: {
    reason: string
    message?: string | null
    details?: Record<string, unknown>
  } | null
}

export class AdvancedSearchHTTPError extends Error {
  readonly status: number
  readonly code: string
  readonly details: Record<string, unknown>
  readonly traceId: string

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown>,
    traceId: string,
  ) {
    super(`${status} ${code}: ${message}`)
    this.status = status
    this.code = code
    this.details = details
    this.traceId = traceId
  }
}

export async function verifyToken(token: string): Promise<VerifyResult> {
  const response = await fetchResponse(buildUrl('/search/advanced/verify-token'), {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  })
  const body = await response.json().catch(() => ({})) as
    | VerifyResult
    | {
      trace_id?: string
      error?: { code?: string; message?: string; details?: Record<string, unknown> }
    }
  if (response.status === 401) {
    return body as VerifyResult
  }
  if (!response.ok) {
    throw new AdvancedSearchHTTPError(
      response.status,
      (body as { error?: { code?: string } }).error?.code ?? 'UNKNOWN',
      (body as { error?: { message?: string } }).error?.message ?? '',
      (body as { error?: { details?: Record<string, unknown> } }).error?.details ?? {},
      (body as { trace_id?: string }).trace_id ?? '',
    )
  }
  return body as VerifyResult
}

function buildQueryString(params: AdvancedSearchParams): string {
  const parts: string[] = [`q=${encodeURIComponent(params.q)}`]
  if (params.topN !== undefined) parts.push(`top_n=${params.topN}`)
  if (params.mmrLambda !== undefined) parts.push(`mmr_lambda=${params.mmrLambda}`)
  if (params.rerank !== undefined) parts.push(`rerank=${params.rerank}`)
  const filters = params.filters
  if (filters) {
    if (filters.year) parts.push(`filters.year=${encodeURIComponent(filters.year)}`)
    for (const venue of filters.venues ?? []) parts.push(`filters.venue=${encodeURIComponent(venue)}`)
    for (const author of filters.authors ?? []) parts.push(`filters.authors=${encodeURIComponent(author)}`)
    for (const keyword of filters.keywords ?? []) parts.push(`filters.keywords=${encodeURIComponent(keyword)}`)
    for (const tag of filters.tags ?? []) parts.push(`filters.tags=${encodeURIComponent(tag)}`)
    if (filters.lang) parts.push(`filters.lang=${encodeURIComponent(filters.lang)}`)
  }
  return parts.join('&')
}

export async function advancedSearch(
  params: AdvancedSearchParams,
  token: string,
): Promise<AdvancedSearchResponse> {
  const response = await fetchResponse(
    `${buildUrl('/search/advanced')}?${buildQueryString(params)}`,
    {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}` },
      timeoutMs: ADVANCED_SEARCH_TIMEOUT_MS,
      retry: 0,
    },
  )
  if (!response.ok) {
    const body = await response.json().catch(() => ({})) as {
      trace_id?: string
      error?: { code?: string; message?: string; details?: Record<string, unknown> }
    }
    throw new AdvancedSearchHTTPError(
      response.status,
      body.error?.code ?? 'UNKNOWN',
      body.error?.message ?? '',
      body.error?.details ?? {},
      body.trace_id ?? '',
    )
  }
  return await response.json() as AdvancedSearchResponse
}
