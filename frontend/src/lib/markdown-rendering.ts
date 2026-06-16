import DOMPurify from 'dompurify'

const safeMermaidSvgUriPattern = /^(?:(?:https?):|\/(?!\/)|#|\.{1,2}\/|[^a-z])/i
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
  const sanitized = DOMPurify.sanitize(String(svg || ''), {
    USE_PROFILES: { svg: true, svgFilters: true },
    FORBID_TAGS: ['script', 'foreignObject', 'style'],
    FORBID_ATTR: forbiddenRendererAttrs,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: safeMermaidSvgUriPattern,
  })
  if (typeof document === 'undefined') return sanitized

  const template = document.createElement('template')
  template.innerHTML = sanitized
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
