import { describe, expect, it } from 'vitest'
import { renderSnippet } from '@/lib/snippet'
import { normalizeSummaryText, summaryParagraphs } from '@/lib/summary-text'

describe('summary text normalization', () => {
  it('renders escaped line breaks and HTML paragraphs as readable paragraphs', () => {
    const summary = String.raw`First line\nSecond line<p>Third paragraph</p><p>Fourth paragraph</p>`

    expect(normalizeSummaryText(summary)).toBe(
      'First line\nSecond line\n\nThird paragraph\n\nFourth paragraph'
    )
    expect(summaryParagraphs(summary)).toEqual([
      'First line\nSecond line',
      'Third paragraph',
      'Fourth paragraph',
    ])
    const lineBreakSummary = 'First\\nSecond'
    expect(renderSnippet(normalizeSummaryText(lineBreakSummary))).toContain('<br>')
  })
})
