import { describe, expect, it } from 'vitest'
import { renderMermaidCodeBlocks } from '@/lib/mermaid-renderer'

describe('renderMermaidCodeBlocks', () => {
  it('replaces Mermaid code fences with sanitized SVG output', async () => {
    const container = document.createElement('div')
    container.innerHTML = [
      '<p>before</p>',
      '<pre><code class="language-mermaid">flowchart TD\\n  api_source --&gt; api_target</code></pre>',
      '<p>after</p>',
    ].join('')

    const result = await renderMermaidCodeBlocks(container, {
      idPrefix: 'test-mermaid',
      theme: 'light',
      renderer: {
        initialize() {},
        async render() {
          return {
            svg: '<svg role="img"><text>api_source to api_target</text><script>alert(1)</script></svg>',
          }
        },
      },
      sanitizeSvg(svg) {
        return svg.replace(/<script[\s\S]*?<\/script>/gi, '')
      },
    })

    const diagnostics = container.innerHTML

    expect(result).toEqual({ rendered: 1, failed: 0, skipped: 0 })
    expect(container.querySelector('pre code.language-mermaid'), diagnostics).toBeNull()
    expect(container.querySelector('.md-editor-mermaid[data-processed] svg'), diagnostics).not.toBeNull()
    expect(container.textContent, diagnostics).toContain('api_source to api_target')
    expect(container.innerHTML, diagnostics).not.toContain('<script')
    expect(container.textContent, diagnostics).toContain('before')
    expect(container.textContent, diagnostics).toContain('after')
  })

  it('keeps the original Mermaid source visible when rendering fails', async () => {
    const container = document.createElement('div')
    container.innerHTML = '<pre><code class="language-mermaid">flowchart TD\\n  broken --> diagram</code></pre>'

    const result = await renderMermaidCodeBlocks(container, {
      idPrefix: 'test-mermaid',
      theme: 'light',
      renderer: {
        initialize() {},
        async render() {
          throw new Error('renderer exploded')
        },
      },
      sanitizeSvg(svg) {
        return svg
      },
    })

    const diagnostics = container.innerHTML

    expect(result).toEqual({ rendered: 0, failed: 1, skipped: 0 })
    expect(container.querySelector('pre code.language-mermaid'), diagnostics).toBeNull()
    expect(container.querySelector('.md-editor-mermaid[data-processed]'), diagnostics).toBeNull()
    expect(container.textContent, diagnostics).toContain('flowchart TD')
    expect(container.textContent, diagnostics).toContain('broken --> diagram')
  })

  it('keeps narrow Mermaid diagrams at their natural viewBox width instead of stretching them', async () => {
    const container = document.createElement('div')
    container.innerHTML = '<pre><code class="language-mermaid">flowchart TD\n  A --> B</code></pre>'

    await renderMermaidCodeBlocks(container, {
      idPrefix: 'test-mermaid',
      theme: 'light',
      renderer: {
        initialize() {},
        async render() {
          return {
            svg: '<svg width="100%" viewBox="0 0 240 120"><g transform="translate(120,60)"><text>A to B</text></g></svg>',
          }
        },
      },
      sanitizeSvg(svg) {
        return svg
      },
    })

    const svg = container.querySelector('.md-editor-mermaid[data-processed] svg') as SVGSVGElement | null
    const diagnostics = container.innerHTML

    expect(svg, diagnostics).not.toBeNull()
    expect(svg?.style.width, diagnostics).toBe('240px')
    expect(svg?.style.maxWidth, diagnostics).toBe('100%')
    expect(svg?.style.height, diagnostics).toBe('auto')
  })
})
