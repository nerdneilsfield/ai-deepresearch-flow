import fc from 'fast-check'
import { describe, expect, it } from 'vitest'

import type { PaperDetail, SearchItem } from '@/types/api'
import {
  buildFolderName,
  resolveSummaryUrls,
  sanitizeSegment,
  selectedExportIssueCount,
  type SelectedExportStats,
} from '@/lib/selected-export'

const fcConfig = {
  seed: Number.parseInt(process.env.FAST_CHECK_SEED ?? '20260615', 10),
  numRuns: Number.parseInt(process.env.FAST_CHECK_RUNS ?? '100', 10),
}

function item(overrides: Partial<SearchItem>): SearchItem {
  return {
    paper_id: 'paper-id',
    title: 'title',
    authors: ['Author'],
    year: '2026',
    venue: '',
    ...overrides,
  }
}

function detail(overrides: Partial<PaperDetail>): PaperDetail {
  return {
    paper_id: 'paper-id',
    title: 'title',
    authors: ['Author'],
    year: '2026',
    venue: '',
    institutions: [],
    keywords: [],
    tags: [],
    ...overrides,
  }
}

function containsUnsafePathCharacter(value: string): boolean {
  const forbidden = ['\\', '/', ':', '*', '?', '"', '<', '>', '|']
  return Array.from(value).some((char) => forbidden.includes(char) || char.charCodeAt(0) < 32)
}

describe('selected export fuzz contracts', () => {
  it('sanitizes arbitrary path segments into bounded relative filename segments', () => {
    fc.assert(
      fc.property(fc.string(), fc.integer({ min: 1, max: 80 }), (value, maxLength) => {
        const segment = sanitizeSegment(value, maxLength)

        expect(segment.length).toBeGreaterThan(0)
        expect(segment.length).toBeLessThanOrEqual(Math.max('unknown'.length, maxLength))
        expect(containsUnsafePathCharacter(segment)).toBe(false)
        expect(segment).not.toBe('.')
        expect(segment).not.toBe('..')
      }),
      fcConfig,
    )
  })

  it('builds folder names without absolute or parent-directory path segments', () => {
    fc.assert(
      fc.property(
        fc.record({
          paper_id: fc.string({ minLength: 0, maxLength: 40 }),
          title: fc.string({ minLength: 0, maxLength: 120 }),
          author: fc.string({ minLength: 0, maxLength: 80 }),
          year: fc.oneof(fc.integer({ min: 0, max: 9999 }), fc.string({ minLength: 0, maxLength: 20 })),
        }),
        ({ paper_id, title, author, year }) => {
          const folder = buildFolderName(item({ paper_id, title, authors: [author], year: String(year) }))

          expect(folder.length).toBeGreaterThan(0)
          expect(containsUnsafePathCharacter(folder)).toBe(false)
          expect(folder.split('/')).not.toContain('..')
          expect(folder.startsWith('/')).toBe(false)
        },
      ),
      fcConfig,
    )
  })

  it('resolves summary URLs from detail templates plus observable fallback fields', () => {
    fc.assert(
      fc.property(
        fc.dictionary(fc.string({ minLength: 1, maxLength: 20 }), fc.webUrl(), { maxKeys: 6 }),
        fc.option(fc.webUrl(), { nil: undefined }),
        fc.option(fc.string({ minLength: 1, maxLength: 20 }), { nil: undefined }),
        (summaryUrls, fallbackUrl, preferred) => {
          const urls = resolveSummaryUrls(
            item({ summary_url: fallbackUrl, preferred_summary_template: preferred }),
            detail({ summary_urls: summaryUrls }),
          )

          for (const [template, url] of Object.entries(summaryUrls)) {
            expect(urls[template]).toBe(url)
          }
          if (fallbackUrl) {
            expect(Object.values(urls)).toContain(fallbackUrl)
          }
        },
      ),
      fcConfig,
    )
  })

  it('reports issue count as the sum of observable missing and failed export counts', () => {
    fc.assert(
      fc.property(
        fc.record({
          papersTotal: fc.nat(100),
          papersProcessed: fc.nat(100),
          filesAdded: fc.nat(100),
          jsonlRows: fc.nat(100),
          missingAssets: fc.nat(100),
          failedAssets: fc.nat(100),
          missingSummaries: fc.nat(100),
          failedSummaries: fc.nat(100),
          metadataFailures: fc.nat(100),
        }),
        (stats: SelectedExportStats) => {
          expect(selectedExportIssueCount(stats)).toBe(
            stats.missingAssets
              + stats.failedAssets
              + stats.missingSummaries
              + stats.failedSummaries
              + stats.metadataFailures,
          )
        },
      ),
      fcConfig,
    )
  })
})
