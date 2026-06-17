import { describe, expect, it } from 'vitest'
import {
  normalizeMathLayout,
  normalizeMermaidLineBreaks,
  sanitizeMermaidSvgContent,
} from '@/lib/markdown-rendering'

describe('markdown rendering helpers', () => {
  it('lifts complex inline formulas out of prose so they can render as readable blocks', () => {
    const source = [
      '核心公式为位置初始化$\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)$和深度合成$\\hat{D}_s(p)=\\sum_{j\\in N_s(p)}\\mu_j^z\\alpha_j\\prod_{k=1}^{j-1}(1-\\alpha_k)$；学生模型继续训练。',
    ].join('\n')

    expect(normalizeMathLayout(source)).toBe([
      '核心公式为位置初始化',
      '',
      '$$',
      '\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)',
      '$$',
      '',
      '和深度合成',
      '',
      '$$',
      '\\hat{D}_s(p)=\\sum_{j\\in N_s(p)}\\mu_j^z\\alpha_j\\prod_{k=1}^{j-1}(1-\\alpha_k)',
      '$$',
      '',
      '；学生模型继续训练。',
    ].join('\n'))
  })

  it('keeps short inline formulas inline', () => {
    expect(normalizeMathLayout('深度图 $D_t$ 和掩码 $M_o$ 保持行内。')).toBe(
      '深度图 $D_t$ 和掩码 $M_o$ 保持行内。',
    )
  })

  it('does not rewrite formulas inside fenced code blocks', () => {
    const source = [
      '正文 $\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)$',
      '',
      '```markdown',
      '代码 $\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)$',
      '```',
    ].join('\n')

    expect(normalizeMathLayout(source)).toBe([
      '正文',
      '',
      '$$',
      '\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)',
      '$$',
      '',
      '```markdown',
      '代码 $\\mu_i=K^{-1}\\dot{p}D_t(p)+\\Delta(p)$',
      '```',
    ].join('\n'))
  })

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

  it('removes active content and remote CSS from Mermaid SVG', () => {
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
    expect(sanitized).toContain('text { fill: red; }')
    expect(sanitized).not.toContain('@import')
    expect(sanitized).not.toContain('<script')
    expect(sanitized).not.toContain('foreignObject')
    expect(sanitized).not.toContain('javascript:')
    expect(sanitized).not.toContain('onclick')
    expect(sanitized).not.toContain('data:image')
  })

  it('preserves safe Mermaid SVG styles while removing active CSS', () => {
    const sanitized = sanitizeMermaidSvgContent(`
      <svg xmlns="http://www.w3.org/2000/svg">
        <style>
          @import url("https://attacker.invalid/x.css");
          .node rect { fill: #ffffff; stroke: #475569; }
          .edgePath path { stroke: #94a3b8; }
          text { font-family: sans-serif; }
          .bad { background-image: url("javascript:alert(1)"); }
        </style>
        <g class="node"><rect /><text>safe label</text></g>
      </svg>
    `)

    expect(sanitized).toContain('safe label')
    expect(sanitized).toContain('.node rect')
    expect(sanitized).toContain('fill: #ffffff')
    expect(sanitized).not.toContain('@import')
    expect(sanitized).not.toContain('attacker.invalid')
    expect(sanitized).not.toContain('javascript:')
    expect(sanitized).not.toContain('background-image')
  })

  it('preserves Mermaid SVG placement attributes while sanitizing active content', () => {
    const sanitized = sanitizeMermaidSvgContent(`
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100">
        <g class="node" transform="translate(100,50)">
          <rect width="80" height="40" />
          <text text-anchor="middle">safe label</text>
        </g>
        <script>alert(1)</script>
      </svg>
    `)

    expect(sanitized).toContain('safe label')
    expect(sanitized).toContain('transform="translate(100,50)"')
    expect(sanitized).toContain('text-anchor="middle"')
    expect(sanitized).not.toContain('<script')
  })

  it('preserves safe Mermaid edge and arrowhead geometry', () => {
    const sanitized = sanitizeMermaidSvgContent(`
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path class="arrowMarkerPath" d="M 0 0 L 10 5 L 0 10 z" />
          </marker>
        </defs>
        <path class="flowchart-link" d="M 10 40 C 40 10, 80 10, 110 40" marker-end="url(#arrow)" />
      </svg>
    `)

    const template = document.createElement('template')
    template.innerHTML = sanitized
    const edge = template.content.querySelector('.flowchart-link')
    const arrow = template.content.querySelector('.arrowMarkerPath')
    const marker = template.content.querySelector('marker')

    expect(edge?.getAttribute('d'), sanitized).toBe('M 10 40 C 40 10, 80 10, 110 40')
    expect(edge?.getAttribute('marker-end'), sanitized).toBe('url(#arrow)')
    expect(arrow?.getAttribute('d'), sanitized).toBe('M 0 0 L 10 5 L 0 10 z')
    expect(marker?.getAttribute('orient'), sanitized).toBe('auto')
  })

  it('preserves safe Mermaid presentation attributes used by non-flowchart diagrams', () => {
    const sanitized = sanitizeMermaidSvgContent(`
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80">
        <defs>
          <linearGradient id="grad" gradientUnits="objectBoundingBox">
            <stop offset="0%" stop-color="hsl(210, 0%, 88%)" />
          </linearGradient>
          <marker id="seq-arrow" markerUnits="userSpaceOnUse" viewBox="0 0 10 10" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="none" stroke="black" stroke-width="1.5" />
          </marker>
        </defs>
        <symbol id="icon" fill-rule="evenodd" clip-rule="evenodd"><path d="M0 0 L1 1" /></symbol>
        <text alignment-baseline="central"><tspan font-style="normal" font-weight="700">label</tspan></text>
        <circle r="4" fill="transparent" stroke="currentColor" />
      </svg>
    `)

    const template = document.createElement('template')
    template.innerHTML = sanitized

    expect(template.content.querySelector('marker')?.getAttribute('markerUnits'), sanitized).toBe('userSpaceOnUse')
    expect(template.content.querySelector('marker path')?.getAttribute('fill'), sanitized).toBe('none')
    expect(template.content.querySelector('marker path')?.getAttribute('stroke'), sanitized).toBe('black')
    expect(template.content.querySelector('marker path')?.getAttribute('stroke-width'), sanitized).toBe('1.5')
    expect(template.content.querySelector('linearGradient')?.getAttribute('gradientUnits'), sanitized).toBe('objectBoundingBox')
    expect(template.content.querySelector('stop')?.getAttribute('stop-color'), sanitized).toBe('hsl(210, 0%, 88%)')
    expect(template.content.querySelector('symbol')?.getAttribute('fill-rule'), sanitized).toBe('evenodd')
    expect(template.content.querySelector('symbol')?.getAttribute('clip-rule'), sanitized).toBe('evenodd')
    expect(template.content.querySelector('text')?.getAttribute('alignment-baseline'), sanitized).toBe('central')
    expect(template.content.querySelector('tspan')?.getAttribute('font-style'), sanitized).toBe('normal')
    expect(template.content.querySelector('tspan')?.getAttribute('font-weight'), sanitized).toBe('700')
    expect(template.content.querySelector('circle')?.getAttribute('fill'), sanitized).toBe('transparent')
    expect(template.content.querySelector('circle')?.getAttribute('stroke'), sanitized).toBe('currentColor')
  })
})
