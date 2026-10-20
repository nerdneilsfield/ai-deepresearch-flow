import { describe, expect, it } from 'vitest'
import {
  appendCondition,
  buildExpression,
  freeTerms,
  parseSearchExpression,
  removeToken,
  splitHighlight,
  toSemanticQuery,
  type ExprToken,
} from '@/lib/search-expression'

const first = (q: string) => parseSearchExpression(q).tokens[0] as ExprToken
const form = {
  allWords: '', phrase: '', anyWords: '', withoutWords: '', author: '', venue: '', yearFrom: '', yearTo: '',
}

describe('parseSearchExpression', () => {
  it('splits free text from field conditions, with quoted values', () => {
    const parsed = parseSearchExpression('transformer author:"vaswani" year:2017..')
    expect(parsed.freeText).toBe('transformer')
    expect(parsed.tokens.map((t) => [t.field, t.value, t.negated])).toEqual([
      ['author', 'vaswani', false],
      ['year', '2017..', false],
    ])
    expect(parsed.tokens[1]).toMatchObject({ yearFrom: 2017, yearTo: null, invalid: false })
  })

  it('keeps spaces inside quoted values and treats field names case-insensitively', () => {
    expect(first('AUTHOR:"geoffrey hinton"')).toMatchObject({ field: 'author', value: 'geoffrey hinton', quoted: true })
  })

  it('parses the four year forms', () => {
    const year = (v: string) => first(`year:${v}`)
    expect(year('2019')).toMatchObject({ yearFrom: 2019, yearTo: 2019, invalid: false })
    expect(year('2019..2023')).toMatchObject({ yearFrom: 2019, yearTo: 2023, invalid: false })
    expect(year('2019..')).toMatchObject({ yearFrom: 2019, yearTo: null, invalid: false })
    expect(year('..2023')).toMatchObject({ yearFrom: null, yearTo: 2023, invalid: false })
  })

  it('flags invalid years', () => {
    for (const bad of ['abc', '20', '2023..2019', '..', '2019..x']) {
      expect(first(`year:${bad}`).invalid, bad).toBe(true)
    }
  })

  it('recognises exclusions of terms, phrases and fields', () => {
    const parsed = parseSearchExpression('-bert -"large model" -author:lecun keep')
    expect(parsed.freeText).toBe('keep')
    expect(parsed.tokens.map((t) => [t.field, t.value, t.negated, t.quoted])).toEqual([
      [null, 'bert', true, false],
      [null, 'large model', true, true],
      ['author', 'lecun', true, false],
    ])
  })

  it('treats unknown fields as ordinary words', () => {
    const parsed = parseSearchExpression('foo:bar')
    expect(parsed.tokens).toEqual([])
    expect(parsed.freeText).toBe('foo:bar')
  })

  it('keeps quoted phrases in free text and drops dangling operators', () => {
    expect(parseSearchExpression('"deep learning" AND author:x').freeText).toBe('"deep learning"')
    expect(parseSearchExpression('a OR author:x OR b').freeText).toBe('a OR b')
    expect(parseSearchExpression('author:x OR a').freeText).toBe('a')
  })
})

describe('removeToken', () => {
  it('removes a token from the start, middle and end without leaving stray spaces', () => {
    const q = 'author:"x y" mid year:2019'
    const [author, year] = parseSearchExpression(q).tokens as [ExprToken, ExprToken]
    expect(removeToken(q, author)).toBe('mid year:2019')
    expect(removeToken(q, year)).toBe('author:"x y" mid')
    expect(removeToken('a year:2019 b', first('a year:2019 b'))).toBe('a b')
    expect(removeToken('-bert', first('-bert'))).toBe('')
  })
})

describe('appendCondition', () => {
  it('appends quoted facet values and bare years', () => {
    expect(appendCondition('', 'author', 'Geoffrey Hinton')).toBe('author:"Geoffrey Hinton"')
    expect(appendCondition('cnn ', 'year', '2019')).toBe('cnn year:2019')
  })

  it('ignores a duplicate condition but replaces an existing year', () => {
    expect(appendCondition('author:"hinton"', 'author', 'Hinton')).toBe('author:"hinton"')
    expect(appendCondition('cnn year:2015..2017', 'year', '2019')).toBe('cnn year:2019')
  })

  it('strips double quotes from the value', () => {
    expect(appendCondition('', 'venue', 'IC"LR')).toBe('venue:"ICLR"')
  })
})

describe('buildExpression', () => {
  it('builds the Scholar form into an expression', () => {
    expect(buildExpression({
      allWords: 'deep learning',
      phrase: 'neural net',
      anyWords: 'cnn rnn',
      withoutWords: 'survey',
      author: 'Hinton',
      venue: 'ICLR',
      yearFrom: '2015',
      yearTo: '2020',
    })).toBe('deep learning "neural net" cnn OR deep learning "neural net" rnn -survey author:"Hinton" venue:"ICLR" year:2015..2020')
  })

  it('supports open and single-year ranges and any-words only', () => {
    expect(buildExpression({ ...form, yearFrom: '2015' })).toBe('year:2015..')
    expect(buildExpression({ ...form, yearTo: '2020' })).toBe('year:..2020')
    expect(buildExpression({ ...form, yearFrom: '2019', yearTo: '2019' })).toBe('year:2019')
    expect(buildExpression({ ...form, anyWords: 'cnn rnn' })).toBe('cnn OR rnn')
    expect(buildExpression(form)).toBe('')
  })
})

describe('toSemanticQuery', () => {
  it('maps supported conditions to filters and flags the rest as ignored', () => {
    const q = 'vit author:"Geoffrey Hinton" venue:ICLR keyword:CNN tag:NLP year:2019.. -x title:abc institution:mit'
    const parsed = parseSearchExpression(q)
    const semantic = toSemanticQuery(parsed)
    expect(semantic.q).toBe('vit')
    expect(semantic.filters).toEqual({
      year: '2019..9999',
      venues: ['ICLR'],
      authors: ['geoffrey hinton'],
      keywords: ['cnn'],
      tags: ['nlp'],
    })
    const ignoredFields = parsed.tokens.filter((t) => semantic.ignored.has(t.start)).map((t) => t.value)
    expect(ignoredFields).toEqual(['x', 'abc', 'mit'])
  })

  it('maps single and open-start years', () => {
    expect(toSemanticQuery(parseSearchExpression('a year:2019')).filters.year).toBe('2019')
    expect(toSemanticQuery(parseSearchExpression('a year:..2023')).filters.year).toBe('0000..2023')
  })
})

describe('freeTerms / splitHighlight', () => {
  it('extracts words and phrases but not operators', () => {
    expect(freeTerms('"neural net" cnn OR rnn')).toEqual(['neural net', 'cnn', 'rnn'])
  })

  it('marks case-insensitive hits and escapes regex characters', () => {
    expect(splitHighlight('Deep learning is deep', ['deep'])).toEqual([
      { text: 'Deep', hit: true },
      { text: ' learning is ', hit: false },
      { text: 'deep', hit: true },
    ])
    expect(splitHighlight('use c++ now', ['c++'])).toEqual([
      { text: 'use ', hit: false },
      { text: 'c++', hit: true },
      { text: ' now', hit: false },
    ])
    expect(splitHighlight('plain', [])).toEqual([{ text: 'plain', hit: false }])
  })
})
