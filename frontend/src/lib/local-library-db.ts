import { SearchItemSchema, type SearchItem } from '@/types/api'
import { isFavoriteRating, type FavoriteRecord } from '@/types/favorites'
import { parseStoredWebDavSyncSettings } from '@/lib/webdav-settings'
import { isManualSyncTimestamp, type ManualSyncMetadata, type WebDavSyncSettings } from '@/types/manual-sync'

const DB_NAME = 'DeepResearchDB'
const DB_VERSION = 3
const SELECTION_STORE_NAME = 'selection'
const FAVORITES_STORE_NAME = 'favorites'
const MANUAL_SYNC_STORE_NAME = 'manual-sync'

interface SelectionDbItem {
  paper_id: string
  data: string
  added_at: number
}

interface FavoriteDbItem {
  paper_id: string
  data: string
  rating: number
  created_at: number
  updated_at: number
}

interface ManualSyncDbItem {
  key: string
  data: string
}

let dbPromise: Promise<IDBDatabase> | null = null

function ensureSelectionStore(db: IDBDatabase) {
  if (!db.objectStoreNames.contains(SELECTION_STORE_NAME)) {
    const store = db.createObjectStore(SELECTION_STORE_NAME, { keyPath: 'paper_id' })
    store.createIndex('added_at', 'added_at', { unique: false })
  }
}

function ensureFavoritesStore(db: IDBDatabase) {
  if (!db.objectStoreNames.contains(FAVORITES_STORE_NAME)) {
    const store = db.createObjectStore(FAVORITES_STORE_NAME, { keyPath: 'paper_id' })
    store.createIndex('rating', 'rating', { unique: false })
    store.createIndex('updated_at', 'updated_at', { unique: false })
  }
}

function ensureManualSyncStore(db: IDBDatabase) {
  if (!db.objectStoreNames.contains(MANUAL_SYNC_STORE_NAME)) {
    db.createObjectStore(MANUAL_SYNC_STORE_NAME, { keyPath: 'key' })
  }
}

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise

  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    let settled = false

    const fail = (error: unknown) => {
      if (settled) return
      settled = true
      dbPromise = null
      reject(error)
    }

    request.onerror = () => {
      fail(request.error)
    }
    request.onblocked = () => {
      fail(new Error('IndexedDB upgrade is blocked by another open tab'))
    }
    request.onupgradeneeded = () => {
      const db = request.result
      ensureSelectionStore(db)
      ensureFavoritesStore(db)
      ensureManualSyncStore(db)
    }
    request.onsuccess = () => {
      const db = request.result
      if (settled) {
        db.close()
        return
      }
      settled = true
      db.onversionchange = () => {
        db.close()
        dbPromise = null
      }
      resolve(db)
    }
  })

  return dbPromise
}

function parseFavorite(item: FavoriteDbItem): FavoriteRecord | null {
  if (!isFavoriteRating(item.rating) || !isManualSyncTimestamp(item.created_at) || !isManualSyncTimestamp(item.updated_at)) {
    return null
  }

  try {
    const paper = SearchItemSchema.safeParse(JSON.parse(item.data))
    if (!paper.success || paper.data.paper_id !== item.paper_id) return null
    return {
      paper: paper.data,
      rating: item.rating,
      createdAt: item.created_at,
      updatedAt: item.updated_at,
    }
  } catch {
    return null
  }
}

function transact<T>(
  storeName: string,
  mode: IDBTransactionMode,
  operation: (store: IDBObjectStore, setResult: (value: T) => void, reject: (reason?: unknown) => void) => void,
): Promise<T> {
  return openDB().then((db) => new Promise<T>((resolve, reject) => {
    let transaction: IDBTransaction
    try {
      transaction = db.transaction(storeName, mode)
    } catch (error) {
      reject(error)
      return
    }
    let result: T
    let hasResult = false
    transaction.oncomplete = () => {
      if (hasResult) resolve(result)
      else reject(new Error(`IndexedDB transaction for ${storeName} completed without a result`))
    }
    transaction.onerror = () => reject(transaction.error)
    transaction.onabort = () => reject(transaction.error)
    operation(transaction.objectStore(storeName), (value) => {
      result = value
      hasResult = true
    }, reject)
  }))
}

export async function loadAllItems(): Promise<unknown[]> {
  try {
    return await transact<unknown[]>(SELECTION_STORE_NAME, 'readonly', (store, resolve, reject) => {
      const request = store.index('added_at').getAll()
      request.onsuccess = () => {
        const items: SearchItem[] = []
        for (const item of request.result as SelectionDbItem[]) {
          try {
            const parsed = SearchItemSchema.safeParse(JSON.parse(item.data))
            if (parsed.success && parsed.data.paper_id === item.paper_id) items.push(parsed.data)
          } catch {
            // Ignore malformed local rows instead of exposing them to the UI.
          }
        }
        resolve(items)
      }
      request.onerror = () => reject(request.error)
    })
  } catch {
    return []
  }
}

export async function saveItem(paperId: string, data: unknown): Promise<void> {
  const parsed = SearchItemSchema.safeParse(data)
  if (!parsed.success || parsed.data.paper_id !== paperId) return
  try {
    await transact<void>(SELECTION_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const item: SelectionDbItem = {
        paper_id: paperId,
        data: JSON.stringify(parsed.data),
        added_at: Date.now(),
      }
      const request = store.put(item)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Keep selection persistence best-effort, matching existing behavior.
  }
}

export async function deleteItem(paperId: string): Promise<void> {
  try {
    await transact<void>(SELECTION_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.delete(paperId)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Keep selection persistence best-effort, matching existing behavior.
  }
}

export async function clearAll(): Promise<void> {
  try {
    await transact<void>(SELECTION_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.clear()
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Keep selection persistence best-effort, matching existing behavior.
  }
}

export async function loadAllFavorites(): Promise<FavoriteRecord[]> {
  try {
    return await transact<FavoriteRecord[]>(FAVORITES_STORE_NAME, 'readonly', (store, resolve, reject) => {
      const request = store.getAll()
      request.onsuccess = () => {
        resolve((request.result as FavoriteDbItem[])
          .map(parseFavorite)
          .filter((item): item is FavoriteRecord => item !== null))
      }
      request.onerror = () => reject(request.error)
    })
  } catch {
    return []
  }
}

export async function saveFavorite(record: FavoriteRecord): Promise<void> {
  const paper = SearchItemSchema.safeParse(record.paper)
  if (
    !paper.success ||
    !isFavoriteRating(record.rating) ||
    !isManualSyncTimestamp(record.createdAt) ||
    !isManualSyncTimestamp(record.updatedAt)
  ) return
  try {
    await transact<void>(FAVORITES_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const item: FavoriteDbItem = {
        paper_id: paper.data.paper_id,
        data: JSON.stringify(paper.data),
        rating: record.rating,
        created_at: record.createdAt,
        updated_at: record.updatedAt,
      }
      const request = store.put(item)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Favorite state remains usable in-memory if browser storage is unavailable.
  }
}

export async function deleteFavorite(paperId: string): Promise<void> {
  try {
    await transact<void>(FAVORITES_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.delete(paperId)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Favorite state remains usable in-memory if browser storage is unavailable.
  }
}

export async function clearFavorites(): Promise<void> {
  try {
    await transact<void>(FAVORITES_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.clear()
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Favorite state remains usable in-memory if browser storage is unavailable.
  }
}

async function loadManualSyncValue<T>(key: string): Promise<T | null> {
  try {
    return await transact<T | null>(MANUAL_SYNC_STORE_NAME, 'readonly', (store, resolve, reject) => {
      const request = store.get(key)
      request.onsuccess = () => {
        const result = request.result as ManualSyncDbItem | undefined
        if (!result) {
          resolve(null)
          return
        }
        try {
          resolve(JSON.parse(result.data) as T)
        } catch {
          resolve(null)
        }
      }
      request.onerror = () => reject(request.error)
    })
  } catch {
    return null
  }
}

async function saveManualSyncValue(key: string, data: unknown): Promise<void> {
  try {
    await transact<void>(MANUAL_SYNC_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.put({ key, data: JSON.stringify(data) } satisfies ManualSyncDbItem)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Sync settings are best-effort local preferences and never contain passwords or passphrases.
  }
}

async function deleteManualSyncValue(key: string): Promise<void> {
  try {
    await transact<void>(MANUAL_SYNC_STORE_NAME, 'readwrite', (store, resolve, reject) => {
      const request = store.delete(key)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Keep the current session usable if local preference storage is unavailable.
  }
}

function parseManualSyncMetadata(value: unknown): ManualSyncMetadata | null {
  if (!value || typeof value !== 'object') return null
  const metadata = value as Partial<ManualSyncMetadata>
  const snapshotCreatedAt = metadata.snapshotCreatedAt
  if (
    typeof metadata.endpoint !== 'string' ||
    (typeof metadata.etag !== 'string' && metadata.etag !== null) ||
    !isManualSyncTimestamp(metadata.syncedAt) ||
    (snapshotCreatedAt !== undefined && snapshotCreatedAt !== null && !isManualSyncTimestamp(snapshotCreatedAt))
  ) {
    return null
  }
  return {
    endpoint: metadata.endpoint,
    etag: metadata.etag,
    syncedAt: metadata.syncedAt,
    snapshotCreatedAt: snapshotCreatedAt ?? null,
  }
}

export async function loadWebDavSyncSettings(): Promise<WebDavSyncSettings | null> {
  const settings = await loadManualSyncValue<unknown>('webdav-settings')
  return parseStoredWebDavSyncSettings(settings)
}

export async function saveWebDavSyncSettings(settings: WebDavSyncSettings): Promise<void> {
  await saveManualSyncValue('webdav-settings', settings)
}

export async function clearWebDavSyncSettings(): Promise<void> {
  await deleteManualSyncValue('webdav-settings')
}

export async function loadManualSyncMetadata(): Promise<ManualSyncMetadata | null> {
  const metadata = await loadManualSyncValue<unknown>('manual-sync-metadata')
  return parseManualSyncMetadata(metadata)
}

export async function saveManualSyncMetadata(metadata: ManualSyncMetadata): Promise<void> {
  await saveManualSyncValue('manual-sync-metadata', metadata)
}

export async function clearManualSyncMetadata(): Promise<void> {
  await deleteManualSyncValue('manual-sync-metadata')
}

export type { SearchItem }
