import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import type { PaperDetail } from '@/types/api'

import {
  clearPaperContentCache,
  readPaperContentRecord,
  touchPaperContentRecord,
  writePaperContentRecord,
} from '@/lib/paper-content-cache'

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

beforeEach(wipeDb)
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
})
