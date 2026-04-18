import { beforeEach, describe, expect, it, vi } from 'vitest'

const { fetchResponseMock } = vi.hoisted(() => ({
  fetchResponseMock: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  buildUrl: (path: string) => `/api/v1${path}`,
  fetchResponse: fetchResponseMock,
}))

import { advancedSearch } from '@/lib/advanced-search'

describe('advancedSearch transport settings', () => {
  beforeEach(() => {
    fetchResponseMock.mockReset()
  })

  it('uses a 120s timeout and disables retry', async () => {
    fetchResponseMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
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
        }),
        {
          status: 200,
          headers: { 'content-type': 'application/json' },
        },
      ),
    )

    await advancedSearch({ q: 'q' }, 'secret')

    expect(fetchResponseMock).toHaveBeenCalledWith(
      '/api/v1/search/advanced?q=q',
      expect.objectContaining({
        method: 'GET',
        retry: 0,
        timeoutMs: 120_000,
      }),
    )
  })
})
