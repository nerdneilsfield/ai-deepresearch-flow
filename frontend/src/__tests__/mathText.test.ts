import { describe, expect, it } from 'vitest'
import { plainMathText, renderMathText } from '@/lib/math-text'

describe('math text', () => {
  it('renders inline TeX in titles and escapes the rest', () => {
    const html = renderMathText('$\\mathbf{SC^2}$ -PCR <b>')
    expect(html).toContain('class="katex"')
    expect(html).not.toContain('$')
    expect(html).toContain('-PCR &lt;b&gt;')
  })

  it('produces readable plain text', () => {
    expect(plainMathText('$\\mathbf{SC^2}$ -PCR: A Title')).toBe('SC^2-PCR: A Title')
    expect(plainMathText('Plain title')).toBe('Plain title')
  })
})

describe('markdown normalize', () => {
  it('rejoins a sentence split by an OCR page-break rule but keeps real rules', async () => {
    const { normalizeMarkdown } = await import('@/lib/markdown-normalize')
    expect(normalizeMarkdown('derived via\n\n---\n\npoints [^15]')).toBe('derived via points [^15]')
    expect(normalizeMarkdown('End.\n\n---\n\nNext')).toBe('End.\n\n---\n\nNext')
  })
})
