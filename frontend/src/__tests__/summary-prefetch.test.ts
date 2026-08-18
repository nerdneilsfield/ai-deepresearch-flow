import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getSummaryPayloadCachedMock } = vi.hoisted(() => ({
  getSummaryPayloadCachedMock: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  getSummaryPayloadCached: getSummaryPayloadCachedMock,
}))

import { prefetchPairedSummary } from '@/lib/summary-prefetch'

describe('prefetchPairedSummary', () => {
  beforeEach(() => {
    getSummaryPayloadCachedMock.mockReset()
    getSummaryPayloadCachedMock.mockResolvedValue({ summary: 'cached' })
  })

  it('warms simple after deep_read is opened', async () => {
    await prefetchPairedSummary('paper-1', 'deep_read', {
      deep_read: 'https://example.com/deep.json',
      simple: 'https://example.com/simple.json',
    })

    expect(getSummaryPayloadCachedMock).toHaveBeenCalledWith(
      'paper-1',
      'simple',
      'https://example.com/simple.json',
    )
  })

  it('warms deep_read after simple is opened', async () => {
    await prefetchPairedSummary('paper-1', 'simple', {
      deep_read: 'https://example.com/deep.json',
      simple: 'https://example.com/simple.json',
    })

    expect(getSummaryPayloadCachedMock).toHaveBeenCalledWith(
      'paper-1',
      'deep_read',
      'https://example.com/deep.json',
    )
  })

  it('skips missing or unrelated templates', async () => {
    await prefetchPairedSummary('paper-1', 'simple', {
      simple: 'https://example.com/simple.json',
    })
    await prefetchPairedSummary('paper-1', 'default', {
      default: 'https://example.com/default.json',
      simple: 'https://example.com/simple.json',
    })

    expect(getSummaryPayloadCachedMock).not.toHaveBeenCalled()
  })
})
