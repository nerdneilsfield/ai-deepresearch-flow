import type { PaperDetail } from '@/types/api'

export interface PaperDetailFreshness {
  manifestUrl: string
  summaryUrl: string
  summaryUrls: Record<string, string>
  translatedMdUrls: Record<string, string>
  sourceMdUrl: string | null
}

export interface CachedSummaryEntry {
  url: string
  payload: Record<string, unknown>
  cachedAt: number
}

export interface CachedTranslationEntry {
  url: string
  markdown: string
  cachedAt: number
}

export interface PaperContentRecord {
  schemaVersion: 1
  paperId: string
  detail: PaperDetail | null
  detailFreshness: PaperDetailFreshness | null
  summaries: Record<string, CachedSummaryEntry>
  translations: Record<string, CachedTranslationEntry>
  lastAccessedAt: number
}

export interface WritePaperContentInput {
  paperId: string
  detail: PaperDetail | null
  detailFreshness: PaperDetailFreshness | null
  summaries: Record<string, CachedSummaryEntry>
  translations: Record<string, CachedTranslationEntry>
  lastAccessedAt?: number
}

const DB_NAME = 'deepresearch_paper_content_cache'
const DB_VERSION = 1
const STORE_NAME = 'paper_content'
const LAST_ACCESSED_INDEX = 'lastAccessedAt'
const SCHEMA_VERSION = 1 as const
const MAX_PAPER_RECORDS = 50
const HOT_CACHE_LIMIT = 2

const hotCache = new Map<string, PaperContentRecord>()

function cloneRecord(record: PaperContentRecord): PaperContentRecord {
  return {
    schemaVersion: SCHEMA_VERSION,
    paperId: record.paperId,
    detail: record.detail ? JSON.parse(JSON.stringify(record.detail)) : null,
    detailFreshness: record.detailFreshness
      ? JSON.parse(JSON.stringify(record.detailFreshness))
      : null,
    summaries: JSON.parse(JSON.stringify(record.summaries)),
    translations: JSON.parse(JSON.stringify(record.translations)),
    lastAccessedAt: record.lastAccessedAt,
  }
}

function cacheHot(record: PaperContentRecord) {
  if (hotCache.has(record.paperId)) {
    hotCache.delete(record.paperId)
  }
  hotCache.set(record.paperId, cloneRecord(record))
  while (hotCache.size > HOT_CACHE_LIMIT) {
    const oldestKey = hotCache.keys().next().value
    if (!oldestKey) break
    hotCache.delete(oldestKey)
  }
}

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      let store: IDBObjectStore
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        store = db.createObjectStore(STORE_NAME, { keyPath: 'paperId' })
      } else {
        store = request.transaction!.objectStore(STORE_NAME)
      }
      if (!store.indexNames.contains(LAST_ACCESSED_INDEX)) {
        store.createIndex(LAST_ACCESSED_INDEX, 'lastAccessedAt', { unique: false })
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IDB open failed'))
  })
}

function readRecordFromStore(
  store: IDBObjectStore,
  paperId: string,
): Promise<PaperContentRecord | null> {
  return new Promise((resolve, reject) => {
    const req = store.get(paperId)
    req.onsuccess = () => {
      const raw = req.result as Partial<PaperContentRecord> | undefined
      if (!raw || raw.schemaVersion !== SCHEMA_VERSION || raw.paperId !== paperId) {
        resolve(null)
        return
      }
      resolve(cloneRecord(raw as PaperContentRecord))
    }
    req.onerror = () => reject(req.error)
  })
}

function getAllRecords(store: IDBObjectStore): Promise<PaperContentRecord[]> {
  return new Promise((resolve, reject) => {
    const req = store.getAll()
    req.onsuccess = () => {
      const records = (req.result as Partial<PaperContentRecord>[])
        .filter(
          (raw): raw is PaperContentRecord =>
            raw.schemaVersion === SCHEMA_VERSION && typeof raw.paperId === 'string',
        )
        .map((record) => cloneRecord(record))
      resolve(records)
    }
    req.onerror = () => reject(req.error)
  })
}

async function enforcePaperLimit(store: IDBObjectStore) {
  const records = await getAllRecords(store)
  if (records.length <= MAX_PAPER_RECORDS) return
  records
    .sort((left, right) => left.lastAccessedAt - right.lastAccessedAt)
    .slice(0, records.length - MAX_PAPER_RECORDS)
    .forEach((record) => {
      store.delete(record.paperId)
      hotCache.delete(record.paperId)
    })
}

export async function readPaperContentRecord(paperId: string): Promise<PaperContentRecord | null> {
  const hot = hotCache.get(paperId)
  if (hot) {
    hotCache.delete(paperId)
    hotCache.set(paperId, hot)
    return cloneRecord(hot)
  }

  try {
    const db = await openDb()
    const record = await readRecordFromStore(db.transaction(STORE_NAME, 'readonly').objectStore(STORE_NAME), paperId)
    db.close()
    if (record) cacheHot(record)
    return record
  } catch {
    return null
  }
}

export async function writePaperContentRecord(input: WritePaperContentInput): Promise<void> {
  const record: PaperContentRecord = {
    schemaVersion: SCHEMA_VERSION,
    paperId: input.paperId,
    detail: input.detail ? JSON.parse(JSON.stringify(input.detail)) : null,
    detailFreshness: input.detailFreshness
      ? JSON.parse(JSON.stringify(input.detailFreshness))
      : null,
    summaries: JSON.parse(JSON.stringify(input.summaries)),
    translations: JSON.parse(JSON.stringify(input.translations)),
    lastAccessedAt: input.lastAccessedAt ?? Date.now(),
  }

  const db = await openDb()
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const putReq = store.put(record)
    putReq.onsuccess = () => {
      void enforcePaperLimit(store).catch(reject)
    }
    putReq.onerror = () => reject(putReq.error)
    tx.oncomplete = () => {
      db.close()
      resolve()
    }
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })

  cacheHot(record)
}

export async function touchPaperContentRecord(paperId: string): Promise<void> {
  const db = await openDb()
  const updated = await new Promise<PaperContentRecord | null>((resolve, reject) => {
    const tx = db.transaction(STORE_NAME, 'readwrite')
    const store = tx.objectStore(STORE_NAME)
    const getReq = store.get(paperId)
    getReq.onsuccess = () => {
      const raw = getReq.result as Partial<PaperContentRecord> | undefined
      if (!raw || raw.schemaVersion !== SCHEMA_VERSION || raw.paperId !== paperId) {
        resolve(null)
        return
      }
      const nextAccessedAt = Math.max(Date.now(), (raw.lastAccessedAt ?? 0) + 1)
      const next: PaperContentRecord = {
        ...(raw as PaperContentRecord),
        lastAccessedAt: nextAccessedAt,
      }
      store.put(next)
      resolve(cloneRecord(next))
    }
    getReq.onerror = () => reject(getReq.error)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error)
  })

  db.close()
  if (updated) cacheHot(updated)
}

export async function clearPaperContentCache(): Promise<void> {
  hotCache.clear()
  try {
    const db = await openDb()
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).clear()
      tx.oncomplete = () => {
        db.close()
        resolve()
      }
      tx.onerror = () => reject(tx.error)
      tx.onabort = () => reject(tx.error)
    })
  } catch {
    // Ignore clear errors for cache storage.
  }
}

function sortRecordEntries(record: Record<string, string>): Array<[string, string]> {
  return Object.entries(record).sort(([left], [right]) => left.localeCompare(right))
}

export function createPaperDetailFreshness(detail: PaperDetail): PaperDetailFreshness {
  return {
    manifestUrl: detail.manifest_url ?? '',
    summaryUrl: detail.summary_url ?? '',
    summaryUrls: { ...(detail.summary_urls ?? {}) },
    translatedMdUrls: { ...(detail.translated_md_urls ?? {}) },
    sourceMdUrl: detail.source_md_url ?? null,
  }
}

export function equalPaperDetailFreshness(
  left: PaperDetailFreshness | null,
  right: PaperDetailFreshness | null,
): boolean {
  if (!left || !right) return left === right
  return (
    left.manifestUrl === right.manifestUrl &&
    left.summaryUrl === right.summaryUrl &&
    left.sourceMdUrl === right.sourceMdUrl &&
    JSON.stringify(sortRecordEntries(left.summaryUrls)) ===
      JSON.stringify(sortRecordEntries(right.summaryUrls)) &&
    JSON.stringify(sortRecordEntries(left.translatedMdUrls)) ===
      JSON.stringify(sortRecordEntries(right.translatedMdUrls))
  )
}
