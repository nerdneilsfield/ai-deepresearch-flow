import DOMPurify from 'dompurify'

const safeMermaidSvgUriPattern = /^(?:(?:https?):|\/(?!\/)|#|\.{1,2}\/|[^a-z])/i
const fencedMarkdownBlockPattern = /(^|\n)(```|~~~)[^\n]*(?:\n[\s\S]*?)?(?:\n\2[^\n]*(?=\n|$)|$)/g
const complexInlineMathMinLength = 28
const complexMathCommands = [
  '\\begin',
  '\\cdot',
  '\\frac',
  '\\int',
  '\\left',
  '\\log',
  '\\max',
  '\\min',
  '\\odot',
  '\\prod',
  '\\rightarrow',
  '\\right',
  '\\sum',
]
const forbiddenRendererAttrs = [
  'style',
  'onerror',
  'onload',
  'onclick',
  'onmouseover',
  'onfocus',
  'onmouseenter',
  'onmouseleave',
]

const unsafeCssRulePattern = /@import\b[^;]*(?:;|$)|[^{};]*\burl\s*\([^)]*\)[^{};]*(?:;|$)|[^{};]*\bexpression\s*\([^)]*\)[^{};]*(?:;|$)|[^{};]*(?:javascript|vbscript|data):[^{};]*(?:;|$)/gi
const mermaidSvgLayoutAttrs = [
  'alignment-baseline',
  'clip-rule',
  'd',
  'dominant-baseline',
  'fill',
  'fill-opacity',
  'fill-rule',
  'font-style',
  'font-weight',
  'gradientUnits',
  'marker-end',
  'marker-mid',
  'marker-start',
  'markerUnits',
  'orient',
  'stop-color',
  'stop-opacity',
  'stroke',
  'stroke-dasharray',
  'stroke-linecap',
  'stroke-linejoin',
  'stroke-miterlimit',
  'stroke-opacity',
  'stroke-width',
  'text-anchor',
  'transform',
]
const mermaidSvgLayoutSelector = 'svg,g,path,line,rect,circle,ellipse,polygon,polyline,text,tspan,marker,defs,clipPath,symbol,linearGradient,stop'

function sanitizeSvgStyleText(styleText: string) {
  return String(styleText || '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(unsafeCssRulePattern, '')
    .trim()
}

function sanitizeSvgStyleBlocks(svg: string) {
  return String(svg || '').replace(
    /<style\b([^>]*)>([\s\S]*?)<\/style>/gi,
    (_match, attrs: string, content: string) => {
      const sanitized = sanitizeSvgStyleText(content)
      return sanitized ? `<style${attrs || ''}>${sanitized}</style>` : ''
    },
  )
}

function isSafeMermaidSvgLayoutAttr(name: string, value: string) {
  const normalized = String(value || '').trim()
  if (!normalized) return false
  if (name === 'fill' || name === 'stroke' || name === 'stop-color') {
    if (/url\s*\(|javascript:|vbscript:|data:|[;<>]/i.test(normalized)) return false
    return /^(?:none|transparent|currentColor|inherit|black|white|red|green|blue|gray|grey|#[0-9a-f]{3,8}|(?:rgb|rgba|hsl|hsla)\([-+\d.,%\s/]+\))$/i.test(normalized)
  }
  if (name === 'fill-opacity' || name === 'stop-opacity' || name === 'stroke-opacity' || name === 'stroke-miterlimit') {
    return /^(?:0(?:\.\d+)?|1(?:\.0+)?|[-+]?\d*\.\d+|[-+]?\d+)$/.test(normalized)
  }
  if (name === 'stroke-width' || name === 'stroke-dasharray') {
    return /^(?:none|[-+]?\d*\.?\d+(?:e[-+]?\d+)?(?:px|em|rem|%)?(?:[\s,]+[-+]?\d*\.?\d+(?:e[-+]?\d+)?(?:px|em|rem|%)?)*)$/i.test(normalized)
  }
  if (name === 'stroke-linecap') {
    return /^(?:butt|round|square|inherit)$/i.test(normalized)
  }
  if (name === 'stroke-linejoin') {
    return /^(?:miter|round|bevel|inherit)$/i.test(normalized)
  }
  if (name === 'fill-rule' || name === 'clip-rule') {
    return /^(?:nonzero|evenodd|inherit)$/i.test(normalized)
  }
  if (name === 'font-style') {
    return /^(?:normal|italic|oblique)$/i.test(normalized)
  }
  if (name === 'font-weight') {
    return /^(?:normal|bold|bolder|lighter|[1-9]00)$/i.test(normalized)
  }
  if (name === 'gradientUnits') {
    return /^(?:userSpaceOnUse|objectBoundingBox)$/i.test(normalized)
  }
  if (name === 'markerUnits') {
    return /^(?:userSpaceOnUse|strokeWidth)$/i.test(normalized)
  }
  if (name === 'alignment-baseline') {
    return /^[a-z-]+$/i.test(normalized)
  }
  if (name === 'd') {
    return /^[MmZzLlHhVvCcSsQqTtAaEe0-9+\-.,\s]+$/.test(normalized)
  }
  if (name === 'transform') {
    return /^(?:\s*(?:matrix|translate|scale|rotate|skewX|skewY)\s*\([-+\d.,\seE]+\)\s*)+$/i.test(normalized)
  }
  if (name === 'orient') {
    return /^(?:auto|auto-start-reverse|[-+]?\d*\.?\d+(?:e[-+]?\d+)?(?:deg|grad|rad|turn)?)$/i.test(normalized)
  }
  if (name === 'text-anchor') {
    return /^(?:start|middle|end|inherit)$/i.test(normalized)
  }
  if (name === 'dominant-baseline') {
    return /^[a-z-]+$/i.test(normalized)
  }
  if (name.startsWith('marker-')) {
    return /^url\(#[A-Za-z0-9_.:-]+\)$/i.test(normalized)
  }
  return false
}

function restoreSafeMermaidSvgLayoutAttrs(sourceSvg: string, sanitizedSvg: string) {
  if (typeof document === 'undefined') return sanitizedSvg
  const sourceTemplate = document.createElement('template')
  const sanitizedTemplate = document.createElement('template')
  sourceTemplate.innerHTML = sourceSvg
  sanitizedTemplate.innerHTML = sanitizedSvg
  const sourceNodes = Array.from(sourceTemplate.content.querySelectorAll<Element>(mermaidSvgLayoutSelector))
  const sanitizedNodes = Array.from(sanitizedTemplate.content.querySelectorAll<Element>(mermaidSvgLayoutSelector))

  sourceNodes.forEach((sourceNode, index) => {
    const sanitizedNode = sanitizedNodes[index]
    if (!sanitizedNode || sourceNode.tagName.toLowerCase() !== sanitizedNode.tagName.toLowerCase()) return
    for (const attr of mermaidSvgLayoutAttrs) {
      const value = sourceNode.getAttribute(attr)
      if (value && isSafeMermaidSvgLayoutAttr(attr, value)) {
        sanitizedNode.setAttribute(attr, value)
      }
    }
  })

  return sanitizedTemplate.innerHTML
}

function isEscapedAt(content: string, index: number) {
  let slashCount = 0
  for (let cursor = index - 1; cursor >= 0 && content[cursor] === '\\'; cursor -= 1) {
    slashCount += 1
  }
  return slashCount % 2 === 1
}

function findUnescapedToken(content: string, token: string, fromIndex: number) {
  let index = content.indexOf(token, fromIndex)
  while (index !== -1) {
    if (!isEscapedAt(content, index)) return index
    index = content.indexOf(token, index + token.length)
  }
  return -1
}

function findInlineDollar(content: string, fromIndex: number) {
  let index = content.indexOf('$', fromIndex)
  while (index !== -1) {
    const previous = index > 0 ? content[index - 1] : ''
    const next = content[index + 1] || ''
    if (!isEscapedAt(content, index) && previous !== '$' && next !== '$') return index
    index = content.indexOf('$', index + 1)
  }
  return -1
}

function findClosingInlineDollar(content: string, fromIndex: number) {
  let index = findInlineDollar(content, fromIndex)
  while (index !== -1) {
    if (!content.slice(fromIndex, index).includes('\n')) return index
    index = findInlineDollar(content, index + 1)
  }
  return -1
}

function shouldLiftInlineMath(content: string) {
  const compact = content.replace(/\s+/g, '')
  if (compact.length >= complexInlineMathMinLength) return true
  if (compact.length < 18) return false
  return complexMathCommands.some((command) => compact.includes(command))
}

function appendMathBlock(output: string, content: string) {
  const math = content.trim()
  if (!math) return output
  let nextOutput = output.replace(/[ \t]+$/g, '')
  if (nextOutput && !nextOutput.endsWith('\n\n')) {
    nextOutput += nextOutput.endsWith('\n') ? '\n' : '\n\n'
  }
  return `${nextOutput}$$\n${math}\n$$`
}

function skipLayoutWhitespaceAfterBlock(content: string, fromIndex: number) {
  let index = fromIndex
  while (content[index] === ' ' || content[index] === '\t') index += 1
  while (content[index] === '\n' || content[index] === '\r') index += 1
  return index
}

function normalizeMathLayoutSegment(content: string) {
  let output = ''
  let index = 0

  while (index < content.length) {
    const displayDollar = findUnescapedToken(content, '$$', index)
    const displayBracket = findUnescapedToken(content, '\\[', index)
    const inlineDollar = findInlineDollar(content, index)
    const candidates = [
      displayDollar === -1 ? Number.POSITIVE_INFINITY : displayDollar,
      displayBracket === -1 ? Number.POSITIVE_INFINITY : displayBracket,
      inlineDollar === -1 ? Number.POSITIVE_INFINITY : inlineDollar,
    ]
    const nextIndex = Math.min(...candidates)
    if (!Number.isFinite(nextIndex)) {
      output += content.slice(index)
      break
    }

    if (nextIndex === displayDollar) {
      const closeIndex = findUnescapedToken(content, '$$', nextIndex + 2)
      if (closeIndex === -1) {
        output += content.slice(index)
        break
      }
      output += content.slice(index, nextIndex)
      output = appendMathBlock(output, content.slice(nextIndex + 2, closeIndex))
      index = skipLayoutWhitespaceAfterBlock(content, closeIndex + 2)
      if (index < content.length) output += '\n\n'
      continue
    }

    if (nextIndex === displayBracket) {
      const closeIndex = findUnescapedToken(content, '\\]', nextIndex + 2)
      if (closeIndex === -1) {
        output += content.slice(index)
        break
      }
      output += content.slice(index, nextIndex)
      output = appendMathBlock(output, content.slice(nextIndex + 2, closeIndex))
      index = skipLayoutWhitespaceAfterBlock(content, closeIndex + 2)
      if (index < content.length) output += '\n\n'
      continue
    }

    const closeIndex = findClosingInlineDollar(content, nextIndex + 1)
    if (closeIndex === -1) {
      output += content.slice(index)
      break
    }
    const math = content.slice(nextIndex + 1, closeIndex)
    if (shouldLiftInlineMath(math)) {
      output += content.slice(index, nextIndex)
      output = appendMathBlock(output, math)
      index = skipLayoutWhitespaceAfterBlock(content, closeIndex + 1)
      if (index < content.length) output += '\n\n'
      continue
    }

    output += content.slice(index, closeIndex + 1)
    index = closeIndex + 1
  }

  return output
}

export function normalizeMathLayout(markdown: string) {
  const protectedBlocks: string[] = []
  const protectedMarkdown = String(markdown || '').replace(
    fencedMarkdownBlockPattern,
    (match: string, leading: string) => {
      const block = leading ? match.slice(leading.length) : match
      const placeholder = `\u0000DRFLOW_FENCE_${protectedBlocks.length}\u0000`
      protectedBlocks.push(block)
      return `${leading || ''}${placeholder}`
    },
  )
  const normalized = normalizeMathLayoutSegment(protectedMarkdown)
  return protectedBlocks.reduce(
    (current, block, blockIndex) => current.replace(`\u0000DRFLOW_FENCE_${blockIndex}\u0000`, block),
    normalized,
  )
}

export function normalizeMermaidLineBreaks(content: string) {
  const labelClosers: Record<string, string> = {
    '[': ']',
    '(': ')',
    '{': '}',
  }
  let output = ''
  let quote: '"' | "'" | null = null
  let escaping = false
  const labelStack: string[] = []

  for (let index = 0; index < content.length;) {
    const char = content[index]
    if (char === undefined) break
    if (escaping) {
      output += char
      escaping = false
      index += 1
      continue
    }
    if (char === '\\') {
      output += char
      escaping = true
      index += 1
      continue
    }
    if (char === '"' || char === "'") {
      quote = quote === char ? null : quote === null ? char : quote
      output += char
      index += 1
      continue
    }
    if (quote === null) {
      const closer = labelClosers[char]
      if (closer) {
        labelStack.push(closer)
        output += char
        index += 1
        continue
      }
      const expectedCloser = labelStack[labelStack.length - 1]
      if (expectedCloser !== undefined && char === expectedCloser) {
        labelStack.pop()
        output += char
        index += 1
        continue
      }
    }

    const breakMatch = content.slice(index).match(/^<br\s*\/?>/i)
    if (breakMatch) {
      output += quote === null && labelStack.length === 0 ? '\n' : breakMatch[0]
      index += breakMatch[0].length
      continue
    }

    output += char
    index += 1
  }

  return output
}

export function sanitizeMermaidSvgContent(svg: string) {
  const svgWithSafeStyles = sanitizeSvgStyleBlocks(svg)
  const sanitized = DOMPurify.sanitize(svgWithSafeStyles, {
    USE_PROFILES: { svg: true, svgFilters: true },
    ADD_TAGS: ['style'],
    ADD_ATTR: [
      'alignment-baseline',
      'aria-roledescription',
      'clip-rule',
      'd',
      'dominant-baseline',
      'fill',
      'fill-opacity',
      'fill-rule',
      'font-style',
      'font-weight',
      'gradientUnits',
      'marker-end',
      'marker-mid',
      'marker-start',
      'markerUnits',
      'orient',
      'role',
      'stop-color',
      'stop-opacity',
      'stroke',
      'stroke-dasharray',
      'stroke-linecap',
      'stroke-linejoin',
      'stroke-miterlimit',
      'stroke-opacity',
      'stroke-width',
      'text-anchor',
      'transform',
    ],
    FORBID_TAGS: ['script', 'foreignObject'],
    FORBID_ATTR: forbiddenRendererAttrs,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: safeMermaidSvgUriPattern,
  })
  const sanitizedWithLayout = restoreSafeMermaidSvgLayoutAttrs(svgWithSafeStyles, String(sanitized))
  if (typeof document === 'undefined') return sanitizedWithLayout

  const template = document.createElement('template')
  template.innerHTML = sanitizedWithLayout
  template.content.querySelectorAll('style').forEach((node) => {
    const sanitizedStyle = sanitizeSvgStyleText(node.textContent || '')
    if (sanitizedStyle) {
      node.textContent = sanitizedStyle
    } else {
      node.remove()
    }
  })
  template.content.querySelectorAll('[href], [xlink\\:href], [src]').forEach((node) => {
    for (const attr of ['href', 'xlink:href', 'src']) {
      const value = node.getAttribute(attr)
      if (value && !safeMermaidSvgUriPattern.test(value)) {
        node.removeAttribute(attr)
      }
    }
  })
  return template.innerHTML
}
