const ESCAPED_LINE_BREAK_RE = /\\r\\n|\\n|\\r/g
const HTML_PARAGRAPH_OPEN_RE = /<\s*p(?:\s+[^>]*)?>/gi
const HTML_PARAGRAPH_CLOSE_RE = /<\s*\/\s*p\s*>/gi
const HTML_LINE_BREAK_RE = /<\s*br\s*\/?\s*>/gi
const HTML_TAG_RE = /<[^>]*>/g

/** Normalize inconsistent summary text before it is rendered as Markdown or plain text. */
export function normalizeSummaryText(value: unknown): string {
  if (typeof value !== 'string') return ''

  return value
    .replace(/\r\n?/g, '\n')
    .replace(ESCAPED_LINE_BREAK_RE, '\n')
    .replace(HTML_LINE_BREAK_RE, '\n')
    .replace(HTML_PARAGRAPH_CLOSE_RE, '\n\n')
    .replace(HTML_PARAGRAPH_OPEN_RE, '\n\n')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** Return safe plain-text paragraphs for compact summary previews. */
export function summaryParagraphs(value: unknown): string[] {
  return normalizeSummaryText(value)
    .replace(HTML_TAG_RE, '')
    .split(/\n{2,}/)
    .map((paragraph) => paragraph.trim())
    .filter(Boolean)
}
