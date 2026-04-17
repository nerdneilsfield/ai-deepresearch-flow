import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  AdvancedSearchHTTPError,
  advancedSearch,
  verifyToken,
} from '@/lib/advanced-search'

const originalFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

function stubJson(status: number, body: unknown) {
  ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  )
}

describe('verifyToken', () => {
  it('returns {valid: true} on 200', async () => {
    stubJson(200, { valid: true })
    expect(await verifyToken('ok')).toEqual({ valid: true })
  })

  it('returns {valid: false, reason: "invalid"} on 401 invalid', async () => {
    stubJson(401, { valid: false, reason: 'invalid' })
    expect(await verifyToken('bad')).toEqual({ valid: false, reason: 'invalid' })
  })

  it('returns {valid: false, reason: "missing"} on 401 missing', async () => {
    stubJson(401, { valid: false, reason: 'missing' })
    expect(await verifyToken('')).toEqual({ valid: false, reason: 'missing' })
  })

  it('sends Authorization: Bearer header', async () => {
    stubJson(200, { valid: true })
    await verifyToken('tok')
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(call).toBeTruthy()
    const [url, init] = call as [string, RequestInit]
    expect(url).toBe('/api/v1/search/advanced/verify-token')
    expect((init as RequestInit).method).toBe('POST')
    expect(new Headers((init as RequestInit).headers).get('authorization')).toBe('Bearer tok')
  })

  it('throws on non-401 server errors instead of treating them as invalid', async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      stubJson(503, {
        success: false,
        trace_id: 't',
        error: { code: 'SERVER_ERROR', message: 'down', details: {} },
      })
    }
    await expect(verifyToken('tok')).rejects.toBeInstanceOf(AdvancedSearchHTTPError)
  })
})

describe('advancedSearch', () => {
  const ok = {
    success: true,
    trace_id: 't',
    query: { raw: 'q', normalized: 'q', applied_filters: {} },
    results: [],
    metadata: {
      counts: {},
      fusion: 'rrf',
      reranker: { applied: false, model: null },
      mmr: { applied: true, lambda: 0.6 },
      embedding: { model: 'bge-m3', dimensions: 1024 },
      latency_ms: {},
    },
    degraded: false,
    degradation: null,
  }

  it('builds query string and header', async () => {
    stubJson(200, ok)
    await advancedSearch(
      {
        q: 'vision',
        topN: 5,
        filters: { year: '2020..2022', venues: ['ICLR'] },
        mmrLambda: 0.6,
        rerank: 'auto',
      },
      'secret',
    )
    const call = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(call).toBeTruthy()
    const url = call?.[0] as string
    expect(url).toContain('q=vision')
    expect(url).toContain('top_n=5')
    expect(url).toContain('filters.year=2020..2022')
    expect(url).toContain('filters.venue=ICLR')
    expect(url).toContain('mmr_lambda=0.6')
    expect(url).toContain('rerank=auto')
  })

  it('returns parsed payload on 200', async () => {
    stubJson(200, ok)
    const out = await advancedSearch({ q: 'q' }, 'secret')
    expect(out.success).toBe(true)
  })

  it('throws AdvancedSearchHTTPError on 401', async () => {
    stubJson(401, {
      success: false,
      trace_id: 't',
      error: { code: 'UNAUTHORIZED', message: 'invalid', details: { reason: 'invalid' } },
    })
    await expect(advancedSearch({ q: 'q' }, 'bad')).rejects.toBeInstanceOf(AdvancedSearchHTTPError)
  })

  it('throws AdvancedSearchHTTPError on 400 invalid filter', async () => {
    stubJson(400, {
      success: false,
      trace_id: 't',
      error: { code: 'INVALID_FILTER', message: 'bad venue', details: {} },
    })
    try {
      await advancedSearch({ q: 'q', filters: { venues: ['drop;table'] } }, 'x')
      expect.fail('should throw')
    } catch (error) {
      expect((error as AdvancedSearchHTTPError).status).toBe(400)
      expect((error as AdvancedSearchHTTPError).code).toBe('INVALID_FILTER')
    }
  })

  it('throws AdvancedSearchHTTPError on 503', async () => {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      stubJson(503, {
        success: false,
        trace_id: 't',
        error: { code: 'VECTOR_STORE_UNAVAILABLE', message: '', details: {} },
      })
    }
    await expect(advancedSearch({ q: 'q' }, 'x')).rejects.toBeInstanceOf(AdvancedSearchHTTPError)
  })
})
