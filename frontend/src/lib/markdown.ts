import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import markdownItFootnote from 'markdown-it-footnote'
import markdownItKatex from 'markdown-it-katex'
import { normalizeMarkdown } from './markdown-normalize'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  breaks: true,
})
md.use(markdownItFootnote)
md.use(markdownItKatex, { throwOnError: false })
md.enable('table')

// @ts-ignore
const PURIFY_CONFIG: any = {
  ALLOWED_TAGS: [
    'p', 'br', 'strong', 'em', 'u', 's', 'del', 'ins', 
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 
    'ul', 'ol', 'li', 'blockquote', 'code', 'pre', 
    'div', 'span', 'a', 'img', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 
    'caption', 'colgroup', 'col', 'sup', 'sub', 'mark',
  ],
  ALLOWED_ATTR: [
    'href', 'title', 'alt', 'src', 'class', 'colspan', 'rowspan', 'align', 'target', 'rel', 'data-footnote',
  ],
  ALLOW_DATA_ATTR: false,
  FORBID_ATTR: ['onerror', 'onload', 'onclick', 'onmouseover'],
}

const renderCache = new Map<string, string>()
const MAX_CACHE_ITEMS = 100

function cacheGet(key: string) {
  const value = renderCache.get(key)
  if (!value) return null
  renderCache.delete(key)
  renderCache.set(key, value)
  return value
}

function cacheSet(key: string, value: string) {
  if (renderCache.has(key)) {
    renderCache.delete(key)
  }
  renderCache.set(key, value)
  if (renderCache.size > MAX_CACHE_ITEMS) {
    const firstKey = renderCache.keys().next().value
    if (firstKey) renderCache.delete(firstKey)
  }
}

export function renderMarkdown(source: string) {
  const cached = cacheGet(source)
  if (cached) return cached
  const normalized = normalizeMarkdown(source || '')
  const raw = md.render(normalized)
  const sanitized = DOMPurify.sanitize(raw, PURIFY_CONFIG)
  const sanitizedStr = typeof sanitized === 'string' ? sanitized : String(sanitized)
  cacheSet(source, sanitizedStr)
  return sanitizedStr
}