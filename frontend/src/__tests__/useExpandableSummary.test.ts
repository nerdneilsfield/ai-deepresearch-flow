import 'fake-indexeddb/auto'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getSummaryPayloadCachedMock, pushToastMock } = vi.hoisted(() => ({
  getSummaryPayloadCachedMock: vi.fn(),
  pushToastMock: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  getSummaryPayloadCached: getSummaryPayloadCachedMock,
}))

vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({
    pushToast: pushToastMock,
  }),
}))

describe('useExpandableSummary', () => {
  beforeEach(() => {
    getSummaryPayloadCachedMock.mockReset()
    pushToastMock.mockReset()
  })

  it('loads expanded summary content through the shared summary cache wrapper', async () => {
    getSummaryPayloadCachedMock.mockResolvedValueOnce({ summary: 'cached summary body' })

    const { useExpandableSummary } = await import('@/composables/useExpandableSummary')
    const summary = useExpandableSummary()

    await summary.toggleSummary({
      paper_id: 'paper-1',
      title: 'Paper 1',
      year: '2026',
      venue: 'ICLR',
      authors: [],
      preferred_summary_template: 'deep_read',
      summary_url: 'https://example.com/summary/paper-1/deep_read.json?v=1',
    })

    expect(getSummaryPayloadCachedMock).toHaveBeenCalledWith(
      'paper-1',
      'deep_read',
      'https://example.com/summary/paper-1/deep_read.json?v=1',
    )
    expect(summary.expandedMarkdown.value['paper-1']).toBe('cached summary body')
    expect(summary.expanded.value['paper-1']).toBe(true)
  })
})
