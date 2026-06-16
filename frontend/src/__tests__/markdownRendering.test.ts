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
})
