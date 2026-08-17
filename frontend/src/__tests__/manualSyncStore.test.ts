import 'fake-indexeddb/auto'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { saveWebDavSyncSettings } from '@/lib/local-library-db'
import { useFavoriteStore } from '@/stores/favorites'
import { useManualSyncStore } from '@/stores/manual-sync'
import { useSelectionStore } from '@/stores/selection'
import type { SearchItem } from '@/types/api'

const DB_NAME = 'DeepResearchDB'

function makePaper(paperId: string): SearchItem {
  return {
    paper_id: paperId,
    title: `Paper ${paperId}`,
    year: '2026',
    venue: 'ICLR',
    authors: ['Ada'],
  }
}

async function wipeDb(): Promise<void> {
  await new Promise<void>((resolve) => {
    const request = indexedDB.deleteDatabase(DB_NAME)
    request.onsuccess = request.onerror = request.onblocked = () => resolve()
  })
}

async function seedVersionTwoLibrary(selected: SearchItem, favorite: SearchItem): Promise<void> {
  const request = indexedDB.open(DB_NAME, 2)
  await new Promise<void>((resolve, reject) => {
    request.onupgradeneeded = () => {
      const selection = request.result.createObjectStore('selection', { keyPath: 'paper_id' })
      selection.createIndex('added_at', 'added_at', { unique: false })
      const favorites = request.result.createObjectStore('favorites', { keyPath: 'paper_id' })
      favorites.createIndex('rating', 'rating', { unique: false })
      favorites.createIndex('updated_at', 'updated_at', { unique: false })
    }
    request.onsuccess = () => {
      const db = request.result
      const transaction = db.transaction(['selection', 'favorites'], 'readwrite')
      transaction.objectStore('selection').put({ paper_id: selected.paper_id, data: JSON.stringify(selected), added_at: 1 })
      transaction.objectStore('favorites').put({
        paper_id: favorite.paper_id,
        data: JSON.stringify(favorite),
        rating: 4,
        created_at: 2,
        updated_at: 3,
      })
      transaction.oncomplete = () => {
        db.close()
        resolve()
      }
      transaction.onerror = () => reject(transaction.error)
    }
    request.onerror = () => reject(request.error)
  })
}

beforeEach(async () => {
  await wipeDb()
  setActivePinia(createPinia())
})

afterEach(async () => {
  vi.restoreAllMocks()
  await wipeDb()
})

describe('manual sync local state', () => {
  it('upgrades a version-two library without changing existing selected papers or favorites', async () => {
    await seedVersionTwoLibrary(makePaper('selected-v2'), makePaper('favorite-v2'))

    const selection = useSelectionStore()
    const favorites = useFavoriteStore()
    const sync = useManualSyncStore()
    await Promise.all([selection.init(), favorites.init(), sync.init()])

    expect(selection.items.map((item) => item.paper_id)).toEqual(['selected-v2'])
    expect(favorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['favorite-v2', 4]])
    expect(sync.settings).toBeNull()
  })

  it('persists only WebDAV configuration and removes it on request', async () => {
    const sync = useManualSyncStore()
    await sync.saveSettings({ endpoint: 'https://cloud.example/paperdb.sync', username: 'ada' })

    expect(sync.settings).toMatchObject({ endpoint: 'https://cloud.example/paperdb.sync', username: 'ada' })

    setActivePinia(createPinia())
    const reloaded = useManualSyncStore()
    await reloaded.init()
    expect(reloaded.settings).toMatchObject({ endpoint: 'https://cloud.example/paperdb.sync', username: 'ada' })

    await reloaded.forgetSettings()
    setActivePinia(createPinia())
    const cleared = useManualSyncStore()
    await cleared.init()
    expect(cleared.settings).toBeNull()
  })

  it('does not load a persisted WebDAV setting that bypasses HTTPS validation', async () => {
    await saveWebDavSyncSettings({
      provider: 'webdav',
      endpoint: 'http://cloud.example/paperdb.sync',
      username: 'ada',
      updatedAt: 1,
    })

    const sync = useManualSyncStore()
    await sync.init()

    expect(sync.settings).toBeNull()
  })

  it('marks an authenticated older download for a second confirmation', async () => {
    const sync = useManualSyncStore()
    sync.metadata = {
      endpoint: 'https://cloud.example/paperdb.sync',
      etag: '"v2"',
      syncedAt: 20,
      snapshotCreatedAt: 20,
    }
    sync.pending = {
      snapshot: {
        type: 'paperdb-manual-sync',
        version: 1,
        createdAt: 10,
        selection: [],
        favorites: [],
      },
      remote: { endpoint: 'https://cloud.example/paperdb.sync', exists: true, etag: '"v1"' },
    }

    expect(sync.pendingIsOlderThanAcknowledged).toBe(true)
  })
})
