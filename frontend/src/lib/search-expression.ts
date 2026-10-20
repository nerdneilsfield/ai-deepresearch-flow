// Mirrors the backend grammar in python/.../snapshot/search_expression.py:
// whitespace-separated tokens, "..." phrases, field:value, -negation. Unknown fields stay free text.
import type { AdvancedSearchFilters } from '@/lib/advanced-search'

export const EXPR_FIELDS = ['author', 'venue', 'keyword', 'tag', 'institution', 'title', 'year'] as const
export type ExprField = (typeof EXPR_FIELDS)[number]

export interface ExprToken {
  /** [start, end) slice of the source query, including a leading "-". */
  start: number
  end: number
  field: ExprField | null
  value: string
  quoted: boolean
  negated: boolean
  /** Only for year tokens; null means open-ended. */
  yearFrom: number | null
  yearTo: number | null
  /** True when a year token cannot be parsed (the backend answers 400). */
  invalid: boolean
}

export interface ParsedExpression {
  tokens: ExprToken[]
  /** Free text with filter/exclusion tokens removed, phrases keep their quotes. */
  freeText: string
}

const FIELD_RE = /([A-Za-z]+):/y
const YEAR_RE = /^[0-9]{4}$/
const isSpace = (ch: string | undefined) => ch !== undefined && /\s/.test(ch)
const last = (list: string[]) => list[list.length - 1] ?? ''
const isOperator = (word: string) => word.toUpperCase() === 'AND' || word.toUpperCase() === 'OR'

function parseYear(value: string): Pick<ExprToken, 'yearFrom' | 'yearTo' | 'invalid'> {
  const bad = { yearFrom: null, yearTo: null, invalid: true }
  const sep = value.indexOf('..')
  const parts: [string, string] = sep < 0 ? [value, value] : [value.slice(0, sep), value.slice(sep + 2)]
  if (sep >= 0 && !parts[0] && !parts[1]) return bad
  if (parts.some((part) => part && !YEAR_RE.test(part))) return bad
  const [from, to] = parts.map((part) => (part ? Number(part) : null)) as [number | null, number | null]
  if (from !== null && to !== null && from > to) return bad
  return { yearFrom: from, yearTo: to, invalid: false }
}

export function parseSearchExpression(q: string): ParsedExpression {
  const tokens: ExprToken[] = []
  const free: string[] = []
  const n = q.length
  let i = 0
  while (i < n) {
    if (isSpace(q[i])) {
      i += 1
      continue
    }
    const start = i
    const negated = q[i] === '-' && i + 1 < n && !isSpace(q[i + 1])
    if (negated) i += 1
    let field: ExprField | null = null
    FIELD_RE.lastIndex = i
    const match = FIELD_RE.exec(q)
    const name = match?.[1]?.toLowerCase()
    if (name && (EXPR_FIELDS as readonly string[]).includes(name)) {
      field = name as ExprField
      i = FIELD_RE.lastIndex
    }
    let value: string
    let quoted = false
    if (q[i] === '"') {
      const close = q.indexOf('"', i + 1)
      const end = close < 0 ? n : close
      value = q.slice(i + 1, end).trim()
      quoted = true
      i = close < 0 ? n : close + 1
    } else {
      let j = i
      while (j < n && !isSpace(q[j]) && q[j] !== '"') j += 1
      value = q.slice(i, j)
      i = j
    }
    if (!value) continue

    if (field || negated) {
      const year = field === 'year' ? parseYear(value) : { yearFrom: null, yearTo: null, invalid: false }
      tokens.push({ start, end: i, field, value, quoted, negated, ...year })
    } else {
      if (!quoted && isOperator(value) && free.length && isOperator(last(free))) free.pop()
      free.push(quoted ? `"${value}"` : value)
    }
  }
  while (free.length && isOperator(last(free))) free.pop()
  while (free.length && isOperator(free[0] ?? '')) free.shift()
  return { tokens, freeText: free.join(' ') }
}

export function removeToken(q: string, token: Pick<ExprToken, 'start' | 'end'>): string {
  const before = q.slice(0, token.start).replace(/\s+$/, '')
  const after = q.slice(token.end).replace(/^\s+/, '')
  return before && after ? `${before} ${after}` : before || after
}

export function formatCondition(field: ExprField, value: string, negated = false): string {
  const clean = value.replace(/"/g, '').trim()
  const body = field === 'year' ? clean : `"${clean}"`
  return `${negated ? '-' : ''}${field}:${body}`
}

/** Append a condition to q. An identical one is left alone; a new year replaces the previous positive year. */
export function appendCondition(q: string, field: ExprField, value: string, negated = false): string {
  const clean = value.replace(/"/g, '').trim()
  const same = (token: ExprToken) => token.field === field && token.negated === negated
  let base = q
  if (field === 'year' && !negated) {
    const olds = parseSearchExpression(base).tokens.filter(same).reverse()
    for (const old of olds) base = removeToken(base, old)
  } else if (parseSearchExpression(base).tokens.some((t) => same(t) && t.value.toLowerCase() === clean.toLowerCase())) {
    return q
  }
  base = base.trimEnd()
  const term = formatCondition(field, clean, negated)
  return base ? `${base} ${term}` : term
}

/** Plain terms of the free text, for highlighting: quoted phrases stay whole, operators are dropped. */
export function freeTerms(freeText: string): string[] {
  const terms: string[] = []
  for (const match of freeText.matchAll(/"([^"]+)"|(\S+)/g)) {
    const term = match[1] ?? match[2] ?? ''
    if (match[1] || !isOperator(term)) terms.push(term)
  }
  return terms
}

export interface HighlightPart {
  text: string
  hit: boolean
}

/** Split text into parts so callers can render hits without v-html. */
export function splitHighlight(text: string, terms: string[]): HighlightPart[] {
  const usable = terms.filter(Boolean).sort((a, b) => b.length - a.length)
  if (!usable.length) return [{ text, hit: false }]
  const pattern = new RegExp(`(${usable.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi')
  return text
    .split(pattern)
    .map((part, index) => ({ text: part, hit: index % 2 === 1 })) // capture group: odd slots are hits
    .filter((part) => part.text)
}

export interface AdvancedFormValues {
  allWords: string
  phrase: string
  anyWords: string
  withoutWords: string
  author: string
  venue: string
  yearFrom: string
  yearTo: string
}

const words = (text: string) => text.trim().split(/\s+/).filter(Boolean)

export function buildExpression(form: AdvancedFormValues): string {
  const phrase = form.phrase.replace(/"/g, '').trim()
  const base = [...words(form.allWords), ...(phrase ? [`"${phrase}"`] : [])]
  const any = words(form.anyWords)
  // AND binds tighter than OR in the FTS query, so "base a OR base b" is the correct distribution.
  const freeText = any.length ? any.map((word) => [...base, word].join(' ')).join(' OR ') : base.join(' ')
  const from = form.yearFrom.trim()
  const to = form.yearTo.trim()
  return [
    freeText,
    ...words(form.withoutWords).map((word) => `-${word.replace(/^-+/, '')}`),
    form.author.trim() ? formatCondition('author', form.author) : '',
    form.venue.trim() ? formatCondition('venue', form.venue) : '',
    from || to ? formatCondition('year', from === to ? from : `${from}..${to}`) : '',
  ]
    .filter(Boolean)
    .join(' ')
}

export interface SemanticQuery {
  q: string
  filters: AdvancedSearchFilters
  /** `start` offsets of tokens the semantic endpoint cannot apply. */
  ignored: Set<number>
}

/** Map a parsed expression onto /search/advanced; authors/keywords/tags are exact lower-case matches there. */
export function toSemanticQuery(parsed: ParsedExpression): SemanticQuery {
  const filters: AdvancedSearchFilters = {}
  const ignored = new Set<number>()
  const push = (key: 'venues' | 'authors' | 'keywords' | 'tags', value: string) => {
    ;(filters[key] ??= []).push(value)
  }
  for (const token of parsed.tokens) {
    if (token.negated || token.invalid) {
      ignored.add(token.start)
    } else if (token.field === 'year') {
      if (filters.year) {
        ignored.add(token.start)
        continue
      }
      // The semantic filter only accepts closed ranges; open ends are widened to the 4-digit extremes.
      const from = String(token.yearFrom ?? 0).padStart(4, '0')
      const to = String(token.yearTo ?? 9999).padStart(4, '0')
      filters.year = from === to ? from : `${from}..${to}`
    } else if (token.field === 'venue') {
      push('venues', token.value)
    } else if (token.field === 'author') {
      push('authors', token.value.toLowerCase())
    } else if (token.field === 'keyword') {
      push('keywords', token.value.toLowerCase())
    } else if (token.field === 'tag') {
      push('tags', token.value.toLowerCase())
    } else {
      ignored.add(token.start)
    }
  }
  return { q: parsed.freeText, filters, ignored }
}
