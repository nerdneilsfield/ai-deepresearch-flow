import { describe, expect, it } from 'vitest'
import {
  normalizeMermaidLineBreaks,
  sanitizeMermaidSvgContent,
} from '@/lib/markdown-rendering'

describe('markdown rendering helpers', () => {
  it('keeps Mermaid line breaks inside quoted labels', () => {
    expect(
      normalizeMermaidLineBreaks('flowchart TD\nA["alpha<br/>beta"] --> B["gamma<br>delta"]'),
    ).toBe('flowchart TD\nA["alpha<br/>beta"] --> B["gamma<br>delta"]')
  })

  it('keeps Mermaid line breaks inside bracket labels', () => {
    expect(
      normalizeMermaidLineBreaks('flowchart TD\nA[alpha<br/>beta] --> B{gamma<br>delta}'),
    ).toBe('flowchart TD\nA[alpha<br/>beta] --> B{gamma<br>delta}')
  })

  it('converts Mermaid line breaks outside labels into source line breaks', () => {
    expect(normalizeMermaidLineBreaks('flowchart TD<br/>  A --> B')).toBe(
      'flowchart TD\n  A --> B',
    )
  })

  it('removes active content and remote inline styling from Mermaid SVG', () => {
    const sanitized = sanitizeMermaidSvgContent(`
      <svg xmlns="http://www.w3.org/2000/svg">
        <style>@import url("https://attacker.invalid/x.css"); text { fill: red; }</style>
        <script>alert(1)</script>
        <foreignObject><div>unsafe html</div></foreignObject>
        <a href="javascript:alert(1)"><text onclick="alert(1)">bad</text></a>
        <image href="data:image/svg+xml;base64,PHN2Zy8+" />
        <text>safe label</text>
      </svg>
    `)

    expect(sanitized).toContain('safe label')
    expect(sanitized).not.toContain('<style')
    expect(sanitized).not.toContain('@import')
    expect(sanitized).not.toContain('<script')
    expect(sanitized).not.toContain('foreignObject')
    expect(sanitized).not.toContain('javascript:')
    expect(sanitized).not.toContain('onclick')
    expect(sanitized).not.toContain('data:image')
  })
})
