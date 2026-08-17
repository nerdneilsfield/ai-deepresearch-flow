import 'fake-indexeddb/auto'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { SearchItem } from '@/types/api'
import { useFavoriteStore } from '@/stores/favorites'
import { useSelectionStore } from '@/stores/selection'

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

async function seedVersionOneSelection(item: SearchItem): Promise<void> {
  const request = indexedDB.open(DB_NAME, 1)
  await new Promise<void>((resolve, reject) => {
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore('selection', { keyPath: 'paper_id' })
      store.createIndex('added_at', 'added_at', { unique: false })
    }
    request.onsuccess = () => {
      const db = request.result
      const transaction = db.transaction('selection', 'readwrite')
      transaction.objectStore('selection').put({
        paper_id: item.paper_id,
        data: JSON.stringify(item),
        added_at: 1,
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

describe('favorite store', () => {
  it('upgrades existing selections, then keeps favorites separate and persistent', async () => {
    const readingPaper = makePaper('reading-paper')
    const favoritePaper = makePaper('favorite-paper')
    await seedVersionOneSelection(readingPaper)

    const selection = useSelectionStore()
    const favorites = useFavoriteStore()
    await selection.init()
    await favorites.add(favoritePaper)

    expect(selection.items.map((item) => item.paper_id)).toEqual(['reading-paper'])
    expect(favorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['favorite-paper', 5]])

    await selection.clear()
    expect(favorites.favoriteIds.has('favorite-paper')).toBe(true)

    setActivePinia(createPinia())
    const reloadedFavorites = useFavoriteStore()
    await reloadedFavorites.init()
    expect(reloadedFavorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['favorite-paper', 5]])
  })

  it('updates ratings, sorts high scores first, and removes only the requested favorite', async () => {
    const favorites = useFavoriteStore()

    await favorites.add(makePaper('five-star'))
    await favorites.add(makePaper('four-star'), 4)
    await favorites.setRating('four-star', 3)

    expect(favorites.sortedItems.map((item) => [item.paper.paper_id, item.rating])).toEqual([
      ['five-star', 5],
      ['four-star', 3],
    ])

    await favorites.remove('four-star')
    expect(favorites.items.map((item) => item.paper.paper_id)).toEqual(['five-star'])

    setActivePinia(createPinia())
    const reloadedFavorites = useFavoriteStore()
    await reloadedFavorites.init()
    expect(reloadedFavorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['five-star', 5]])
  })

  it('merges only newer favorites and replaces the stored list persistently', async () => {
    const favorites = useFavoriteStore()
    await favorites.add(makePaper('shared'), 2)
    const local = favorites.items[0]!
    const importedNewer = {
      ...local,
      rating: 4 as const,
      updatedAt: local.updatedAt + 1,
    }
    const importedNew = {
      paper: makePaper('imported'),
      rating: 5 as const,
      createdAt: 10,
      updatedAt: 10,
    }

    const merged = await favorites.merge([
      { ...local, rating: 1 as const, updatedAt: local.updatedAt - 1 },
      importedNewer,
      importedNew,
    ])

    expect(merged).toBe(2)
    expect(favorites.sortedItems.map((item) => [item.paper.paper_id, item.rating])).toEqual([
      ['imported', 5],
      ['shared', 4],
    ])

    const replaced = await favorites.replace([{
      paper: makePaper('replacement'),
      rating: 3,
      createdAt: 20,
      updatedAt: 20,
    }])
    expect(replaced).toBe(1)
    expect(favorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['replacement', 3]])

    setActivePinia(createPinia())
    const reloadedFavorites = useFavoriteStore()
    await reloadedFavorites.init()
    expect(reloadedFavorites.items.map((item) => [item.paper.paper_id, item.rating])).toEqual([['replacement', 3]])
  })
})

describe('selection list import state', () => {
  it('merges new papers, replaces current papers, and keeps replacement after reload', async () => {
    const selection = useSelectionStore()
    const existing = makePaper('existing')
    const imported = makePaper('imported')
    const replacement = makePaper('replacement')

    await selection.add(existing)
    const merged = await selection.merge([existing, imported, imported])

    expect(merged).toBe(1)
    expect(selection.items.map((item) => item.paper_id)).toEqual(['existing', 'imported'])

    const replaced = await selection.replace([replacement, replacement])
    expect(replaced).toBe(1)
    expect(selection.items.map((item) => item.paper_id)).toEqual(['replacement'])

    setActivePinia(createPinia())
    const reloadedSelection = useSelectionStore()
    await reloadedSelection.init()
    expect(reloadedSelection.items.map((item) => item.paper_id)).toEqual(['replacement'])
  })
})
