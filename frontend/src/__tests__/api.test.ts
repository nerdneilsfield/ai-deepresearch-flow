import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { matchBibtex } from '@/lib/api'

const originalFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})

afterEach(() => {
  globalThis.fetch = originalFetch
})

describe('matchBibtex', () => {
  it('returns validated BibTeX match results', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({
        matched: [
          {
            bibtex_key: 'smith2024',
            paper_id: 'paper-1',
            match_method: 'doi',
            title: 'Paper',
            year: 2024,
            venue: null,
            authors: ['Ada'],
          },
        ],
        unmatched: [],
        stats: { total: 1, matched: 1, unmatched: 0 },
      }), { status: 200 }),
    )

    const result = await matchBibtex('@article{smith2024}')

    expect(result.matched[0]?.year).toBe('2024')
    expect(result.stats.matched).toBe(1)
  })

  it('rejects malformed BibTeX match responses', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({
        matched: [
          {
            bibtex_key: 'smith2024',
            paper_id: 'paper-1',
            match_method: 'unknown',
            title: 'Paper',
            year: '2024',
            venue: null,
            authors: [],
          },
        ],
        unmatched: [],
        stats: { total: 1, matched: 1, unmatched: 0 },
      }), { status: 200 }),
    )

    await expect(matchBibtex('@article{smith2024}')).rejects.toThrow()
  })
})
