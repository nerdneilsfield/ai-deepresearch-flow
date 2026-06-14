import { describe, expect, it } from 'vitest'
import { renderSnippet } from '@/lib/snippet'

describe('renderSnippet', () => {
  it('renders marked markdown snippets without plugin import errors', () => {
    const html = renderSnippet('A [[[highlighted]]] $x$ footnote[^1].\n\n[^1]: note')

    expect(html).toContain('<mark')
    expect(html).toContain('highlighted')
    expect(html).toContain('footnote')
  })
})
