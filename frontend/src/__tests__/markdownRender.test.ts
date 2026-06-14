import { describe, expect, it } from 'vitest'
import { renderMarkdown } from '@/lib/markdown'

describe('renderMarkdown', () => {
  it('renders markdown math and footnotes without throwing on non-strict LaTeX input', () => {
    const html = renderMarkdown('Math $\\textcircled{1}$ and a note[^1].\n\n[^1]: Footnote text')

    expect(html).toContain('Math')
    expect(html).toContain('katex')
    expect(html).toContain('Footnote text')
  })

  it('sanitizes unsafe HTML while preserving safe markdown output', () => {
    const html = renderMarkdown('![x](javascript:alert(1)) <img src=x onerror=alert(1)> **safe**')

    expect(html).toContain('<strong>safe</strong>')
    expect(html).not.toContain('onerror')
    expect(html).not.toContain('href="javascript:')
    expect(html).not.toContain('src="javascript:')
  })
})
