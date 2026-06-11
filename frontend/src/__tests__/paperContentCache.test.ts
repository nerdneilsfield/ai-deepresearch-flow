import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { PaperDetail } from '@/types/api'

import {
  clearPaperContentCache,
  readPaperContentRecord,
  touchPaperContentRecord,
  writePaperContentRecord,
} from '@/lib/paper-content-cache'
import { getPaperDetailCached, getSummaryPayloadCached, getTranslatedMarkdownCached } from '@/lib/api'

const { fetchJsonMock, fetchTextMock } = vi.hoisted(() => ({
  fetchJsonMock: vi.fn(),
  fetchTextMock: vi.fn(),
}))

vi.mock('@/lib/http', () => ({
  buildUrl: (path: string) => path,
  fetchJson: fetchJsonMock,
  fetchText: fetchTextMock,
}))

const DB_NAME = 'deepresearch_paper_content_cache'
const STORE_NAME = 'paper_content'

function makeDetail(paperId: string): PaperDetail {
  return {
    paper_id: paperId,
    title: `Paper ${paperId}`,
    year: '2026',
    venue: 'ICLR',
    authors: ['Alice Example'],
    keywords: [],
    institutions: [],
    tags: [],
    summary_urls: {
      default: `https://example.com/summary/${paperId}/default.json?v=1`,
    },
    translated_md_urls: {
      zh: `https://example.com/md_translate/zh/${paperId}-zh.md`,
    },
    summary_url: `https://example.com/summary/${paperId}.json?v=1`,
    manifest_url: `https://example.com/manifest/${paperId}.json?v=1`,
    source_md_url: `https://example.com/md/${paperId}.md`,
  }
}

async function wipeDb(): Promise<void> {
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase(DB_NAME)
    req.onsuccess = req.onerror = req.onblocked = () => resolve()
  })
}

async function rawWrite(record: unknown, key: string): Promise<void> {
  const request = indexedDB.open(DB_NAME, 1)
  await new Promise<void>((resolve, reject) => {
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'paperId' })
      }
    }
    request.onsuccess = () => {
      const db = request.result
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put({ ...((record as object) ?? {}), paperId: key })
      tx.oncomplete = () => {
        db.close()
        resolve()
      }
      tx.onerror = () => reject(tx.error)
    }
    request.onerror = () => reject(request.error)
  })
}

async function waitForRecord(
  paperId: string,
  predicate: (record: Awaited<ReturnType<typeof readPaperContentRecord>>) => boolean,
): Promise<Awaited<ReturnType<typeof readPaperContentRecord>>> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    const record = await readPaperContentRecord(paperId)
    if (predicate(record)) {
      return record
    }
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
  return readPaperContentRecord(paperId)
}

beforeEach(async () => {
  fetchJsonMock.mockReset()
  fetchTextMock.mockReset()
  await wipeDb()
})
afterEach(wipeDb)

describe('paper-content-cache storage', () => {
  it('writes and reads a paper record by paper_id', async () => {
    await writePaperContentRecord({
      paperId: 'paper-1',
      detail: makeDetail('paper-1'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-1.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-1.json?v=1',
        summaryUrls: {
          default: 'https://example.com/summary/paper-1/default.json?v=1',
        },
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-1-zh.md',
        },
        sourceMdUrl: 'https://example.com/md/paper-1.md',
      },
      summaries: {},
      translations: {},
    })

    const record = await readPaperContentRecord('paper-1')

    expect(record?.paperId).toBe('paper-1')
    expect(record?.detail?.title).toBe('Paper paper-1')
  })

  it('treats schemaVersion mismatch as cache miss', async () => {
    await rawWrite(
      {
        schemaVersion: 999,
        detail: makeDetail('legacy-paper'),
        detailFreshness: {
          manifestUrl: 'https://example.com/manifest/legacy-paper.json?v=1',
          summaryUrl: 'https://example.com/summary/legacy-paper.json?v=1',
          summaryUrls: {},
          translatedMdUrls: {},
          sourceMdUrl: null,
        },
        summaries: {},
        translations: {},
        lastAccessedAt: 1,
      },
      'legacy-paper',
    )

    expect(await readPaperContentRecord('legacy-paper')).toBeNull()
  })

  it('keeps at most 50 paper records', async () => {
    for (let index = 0; index < 51; index += 1) {
      const paperId = `paper-${index}`
      await writePaperContentRecord({
        paperId,
        detail: makeDetail(paperId),
        detailFreshness: {
          manifestUrl: `https://example.com/manifest/${paperId}.json?v=1`,
          summaryUrl: `https://example.com/summary/${paperId}.json?v=1`,
          summaryUrls: {},
          translatedMdUrls: {},
          sourceMdUrl: null,
        },
        summaries: {},
        translations: {},
      })
    }

    const hits = await Promise.all(
      Array.from({ length: 51 }, (_, index) => readPaperContentRecord(`paper-${index}`)),
    )

    expect(hits.filter(Boolean)).toHaveLength(50)
  })

  it('evicts the oldest paper record when the limit is exceeded', async () => {
    for (let index = 0; index < 50; index += 1) {
      const paperId = `paper-${index}`
      await writePaperContentRecord({
        paperId,
        detail: makeDetail(paperId),
        detailFreshness: {
          manifestUrl: `https://example.com/manifest/${paperId}.json?v=1`,
          summaryUrl: `https://example.com/summary/${paperId}.json?v=1`,
          summaryUrls: {},
          translatedMdUrls: {},
          sourceMdUrl: null,
        },
        summaries: {},
        translations: {},
      })
    }

    await writePaperContentRecord({
      paperId: 'paper-50',
      detail: makeDetail('paper-50'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-50.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-50.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
    })

    expect(await readPaperContentRecord('paper-0')).toBeNull()
    expect(await readPaperContentRecord('paper-50')).not.toBeNull()
  })

  it('touch updates access time only for the targeted paper', async () => {
    await writePaperContentRecord({
      paperId: 'paper-a',
      detail: makeDetail('paper-a'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-a.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-a.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
    })
    await writePaperContentRecord({
      paperId: 'paper-b',
      detail: makeDetail('paper-b'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-b.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-b.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
    })

    const beforeA = await readPaperContentRecord('paper-a')
    const beforeB = await readPaperContentRecord('paper-b')

    await touchPaperContentRecord('paper-b')

    const afterA = await readPaperContentRecord('paper-a')
    const afterB = await readPaperContentRecord('paper-b')

    expect(afterA?.lastAccessedAt).toBe(beforeA?.lastAccessedAt)
    expect(afterB?.lastAccessedAt).toBeGreaterThan(beforeB?.lastAccessedAt ?? 0)
  })

  it('clears all records', async () => {
    await writePaperContentRecord({
      paperId: 'paper-1',
      detail: makeDetail('paper-1'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-1.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-1.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
    })

    await clearPaperContentCache()

    expect(await readPaperContentRecord('paper-1')).toBeNull()
  })

  it('reuses cached detail immediately and refreshes changed freshness in the background', async () => {
    const firstAccessAt = 100
    await writePaperContentRecord({
      paperId: 'paper-1',
      detail: {
        ...makeDetail('paper-1'),
        title: 'Cached Title',
      },
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-1.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-1.json?v=1',
        summaryUrls: {
          default: 'https://example.com/summary/paper-1/default.json?v=1',
        },
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-1-zh.md',
        },
        sourceMdUrl: 'https://example.com/md/paper-1.md',
      },
      summaries: {},
      translations: {},
      lastAccessedAt: firstAccessAt,
    })

    let resolveFetch!: (value: PaperDetail) => void
    fetchJsonMock.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve
        }),
    )

    const cachedDetail = await getPaperDetailCached('paper-1')
    const touchedRecord = await readPaperContentRecord('paper-1')

    expect(cachedDetail.title).toBe('Cached Title')
    expect(fetchJsonMock).toHaveBeenCalledTimes(1)
    expect(touchedRecord?.lastAccessedAt).toBeGreaterThan(firstAccessAt)

    resolveFetch({
      ...makeDetail('paper-1'),
      title: 'Fresh Title',
      manifest_url: 'https://example.com/manifest/paper-1.json?v=2',
    })
    const refreshedRecord = await waitForRecord(
      'paper-1',
      (record) => record?.detail?.title === 'Fresh Title',
    )

    expect(refreshedRecord?.detail?.title).toBe('Fresh Title')
    expect(refreshedRecord?.detailFreshness?.manifestUrl).toContain('v=2')
    expect(refreshedRecord?.lastAccessedAt).toBe(touchedRecord?.lastAccessedAt)
  })

  it('treats reordered freshness records as unchanged detail freshness', async () => {
    await writePaperContentRecord({
      paperId: 'paper-2',
      detail: {
        ...makeDetail('paper-2'),
        title: 'Cached Order Stable',
      },
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-2.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-2.json?v=1',
        summaryUrls: {
          b: 'https://example.com/summary/paper-2/b.json?v=1',
          a: 'https://example.com/summary/paper-2/a.json?v=1',
        },
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-2-zh.md',
          en: 'https://example.com/md_translate/en/paper-2-en.md',
        },
        sourceMdUrl: 'https://example.com/md/paper-2.md',
      },
      summaries: {},
      translations: {},
      lastAccessedAt: 10,
    })

    fetchJsonMock.mockResolvedValueOnce({
      ...makeDetail('paper-2'),
      title: 'Fresh Order Stable',
      summary_urls: {
        a: 'https://example.com/summary/paper-2/a.json?v=1',
        b: 'https://example.com/summary/paper-2/b.json?v=1',
      },
      translated_md_urls: {
        en: 'https://example.com/md_translate/en/paper-2-en.md',
        zh: 'https://example.com/md_translate/zh/paper-2-zh.md',
      },
    })

    const cachedDetail = await getPaperDetailCached('paper-2')
    const record = await waitForRecord(
      'paper-2',
      (current) => current?.detail?.title === 'Cached Order Stable',
    )

    expect(cachedDetail.title).toBe('Cached Order Stable')
    expect(record?.detail?.title).toBe('Cached Order Stable')
  })

  it('fetches and writes back the first uncached summary template into an existing paper record', async () => {
    await writePaperContentRecord({
      paperId: 'paper-3',
      detail: makeDetail('paper-3'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-3.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-3.json?v=1',
        summaryUrls: {
          default: 'https://example.com/summary/paper-3/default.json?v=1',
        },
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
      lastAccessedAt: 55,
    })
    fetchJsonMock.mockResolvedValueOnce({ summary: 'template body' })

    const payload = await getSummaryPayloadCached(
      'paper-3',
      'deep_read',
      'https://example.com/summary/paper-3/deep_read.json?v=1',
    )
    const record = await readPaperContentRecord('paper-3')

    expect(payload).toEqual({ summary: 'template body' })
    expect(record?.summaries.deep_read?.payload).toEqual({ summary: 'template body' })
    expect(record?.lastAccessedAt).toBeGreaterThan(55)
  })

  it('keeps a newly opened summary-only paper when the paper cache is already full', async () => {
    for (let index = 0; index < 50; index += 1) {
      const paperId = `full-summary-${index}`
      await writePaperContentRecord({
        paperId,
        detail: makeDetail(paperId),
        detailFreshness: {
          manifestUrl: `https://example.com/manifest/${paperId}.json?v=1`,
          summaryUrl: `https://example.com/summary/${paperId}.json?v=1`,
          summaryUrls: {},
          translatedMdUrls: {},
          sourceMdUrl: null,
        },
        summaries: {},
        translations: {},
        lastAccessedAt: index + 1,
      })
    }
    fetchJsonMock.mockResolvedValueOnce({ summary: 'new paper summary' })

    await getSummaryPayloadCached(
      'fresh-summary-paper',
      'deep_read',
      'https://example.com/summary/fresh-summary-paper/deep_read.json?v=1',
    )

    const newRecord = await readPaperContentRecord('fresh-summary-paper')
    const oldestRecord = await readPaperContentRecord('full-summary-0')

    expect(newRecord?.lastAccessedAt).toBeGreaterThan(0)
    expect(newRecord).not.toBeNull()
    expect(oldestRecord).toBeNull()
  })

  it('reuses cached summary when the template url is unchanged', async () => {
    await writePaperContentRecord({
      paperId: 'paper-4',
      detail: makeDetail('paper-4'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-4.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-4.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {
        deep_read: {
          url: 'https://example.com/summary/paper-4/deep_read.json?v=1',
          payload: { summary: 'cached summary' },
          cachedAt: 1,
        },
      },
      translations: {},
      lastAccessedAt: 77,
    })

    const payload = await getSummaryPayloadCached(
      'paper-4',
      'deep_read',
      'https://example.com/summary/paper-4/deep_read.json?v=1',
    )

    expect(payload).toEqual({ summary: 'cached summary' })
    expect(fetchJsonMock).not.toHaveBeenCalled()
  })

  it('replaces cached summary when the template url changes', async () => {
    await writePaperContentRecord({
      paperId: 'paper-5',
      detail: makeDetail('paper-5'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-5.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-5.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {},
        sourceMdUrl: null,
      },
      summaries: {
        deep_read: {
          url: 'https://example.com/summary/paper-5/deep_read.json?v=1',
          payload: { summary: 'old summary' },
          cachedAt: 1,
        },
      },
      translations: {},
      lastAccessedAt: 88,
    })
    fetchJsonMock.mockResolvedValueOnce({ summary: 'new summary' })

    const payload = await getSummaryPayloadCached(
      'paper-5',
      'deep_read',
      'https://example.com/summary/paper-5/deep_read.json?v=2',
    )
    const record = await readPaperContentRecord('paper-5')

    expect(payload).toEqual({ summary: 'new summary' })
    expect(record?.summaries.deep_read?.url).toContain('v=2')
    expect(record?.summaries.deep_read?.payload).toEqual({ summary: 'new summary' })
  })

  it('fetches and writes back the first uncached translation into an existing paper record', async () => {
    await writePaperContentRecord({
      paperId: 'paper-6',
      detail: makeDetail('paper-6'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-6.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-6.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-6-zh.md',
        },
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {},
      lastAccessedAt: 66,
    })
    fetchTextMock.mockResolvedValueOnce('translated body')

    const markdown = await getTranslatedMarkdownCached(
      'paper-6',
      'zh',
      'https://example.com/md_translate/zh/paper-6-zh.md',
    )
    const record = await readPaperContentRecord('paper-6')

    expect(markdown).toBe('translated body')
    expect(record?.translations.zh?.markdown).toBe('translated body')
    expect(record?.lastAccessedAt).toBeGreaterThan(66)
  })

  it('keeps a newly opened translation-only paper when the paper cache is already full', async () => {
    for (let index = 0; index < 50; index += 1) {
      const paperId = `full-translation-${index}`
      await writePaperContentRecord({
        paperId,
        detail: makeDetail(paperId),
        detailFreshness: {
          manifestUrl: `https://example.com/manifest/${paperId}.json?v=1`,
          summaryUrl: `https://example.com/summary/${paperId}.json?v=1`,
          summaryUrls: {},
          translatedMdUrls: {},
          sourceMdUrl: null,
        },
        summaries: {},
        translations: {},
        lastAccessedAt: index + 1,
      })
    }
    fetchTextMock.mockResolvedValueOnce('fresh translation body')

    await getTranslatedMarkdownCached(
      'fresh-translation-paper',
      'zh',
      'https://example.com/md_translate/zh/fresh-translation-paper-zh.md',
    )

    const newRecord = await readPaperContentRecord('fresh-translation-paper')
    const oldestRecord = await readPaperContentRecord('full-translation-0')

    expect(newRecord?.lastAccessedAt).toBeGreaterThan(0)
    expect(newRecord).not.toBeNull()
    expect(oldestRecord).toBeNull()
  })

  it('reuses cached translated markdown when the url is unchanged', async () => {
    await writePaperContentRecord({
      paperId: 'paper-7',
      detail: makeDetail('paper-7'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-7.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-7.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-7-zh.md',
        },
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {
        zh: {
          url: 'https://example.com/md_translate/zh/paper-7-zh.md',
          markdown: 'cached translation',
          cachedAt: 1,
        },
      },
      lastAccessedAt: 77,
    })

    const markdown = await getTranslatedMarkdownCached(
      'paper-7',
      'zh',
      'https://example.com/md_translate/zh/paper-7-zh.md',
    )

    expect(markdown).toBe('cached translation')
    expect(fetchTextMock).not.toHaveBeenCalled()
  })

  it('replaces cached translated markdown when the url changes', async () => {
    await writePaperContentRecord({
      paperId: 'paper-8',
      detail: makeDetail('paper-8'),
      detailFreshness: {
        manifestUrl: 'https://example.com/manifest/paper-8.json?v=1',
        summaryUrl: 'https://example.com/summary/paper-8.json?v=1',
        summaryUrls: {},
        translatedMdUrls: {
          zh: 'https://example.com/md_translate/zh/paper-8-zh.md',
        },
        sourceMdUrl: null,
      },
      summaries: {},
      translations: {
        zh: {
          url: 'https://example.com/md_translate/zh/paper-8-zh.md',
          markdown: 'old translation',
          cachedAt: 1,
        },
      },
      lastAccessedAt: 88,
    })
    fetchTextMock.mockResolvedValueOnce('new translation')

    const markdown = await getTranslatedMarkdownCached(
      'paper-8',
      'zh',
      'https://example.com/md_translate/zh/paper-8-zh-v2.md',
    )
    const record = await readPaperContentRecord('paper-8')

    expect(markdown).toBe('new translation')
    expect(record?.translations.zh?.url).toContain('v2')
    expect(record?.translations.zh?.markdown).toBe('new translation')
  })
})
