import { defineStore } from 'pinia'
import { clearFavorites, deleteFavorite, loadAllFavorites, saveFavorite } from '@/lib/favorite-db'
import type { SearchItem } from '@/types/api'
import { isFavoriteRating, type FavoriteRating, type FavoriteRecord } from '@/types/favorites'

function uniqueRecords(records: FavoriteRecord[]) {
  const byId = new Map<string, FavoriteRecord>()
  for (const record of records) {
    const existing = byId.get(record.paper.paper_id)
    if (!existing || record.updatedAt >= existing.updatedAt) {
      byId.set(record.paper.paper_id, record)
    }
  }
  return [...byId.values()]
}

export const useFavoriteStore = defineStore('favorites', {
  state: () => ({
    items: [] as FavoriteRecord[],
    _initialized: false,
  }),
  getters: {
    count: (state) => state.items.length,
    favoriteIds: (state) => new Set(state.items.map((item) => item.paper.paper_id)),
    ratingsById: (state) => Object.fromEntries(state.items.map((item) => [item.paper.paper_id, item.rating])) as Record<string, FavoriteRating>,
    ratingFor: (state) => (paperId: string) => state.items.find((item) => item.paper.paper_id === paperId)?.rating,
    sortedItems: (state) => [...state.items].sort((left, right) =>
      right.rating - left.rating || right.updatedAt - left.updatedAt || left.paper.title.localeCompare(right.paper.title),
    ),
  },
  actions: {
    async init() {
      if (this._initialized) return
      this.items = await loadAllFavorites()
      this._initialized = true
    },
    async add(item: SearchItem, rating: FavoriteRating = 5) {
      await this.init()
      if (this.items.some((favorite) => favorite.paper.paper_id === item.paper_id)) return
      const now = Date.now()
      const record: FavoriteRecord = {
        paper: item,
        rating,
        createdAt: now,
        updatedAt: now,
      }
      this.items.push(record)
      await saveFavorite(record)
    },
    async toggle(item: SearchItem) {
      await this.init()
      const existing = this.items.find((favorite) => favorite.paper.paper_id === item.paper_id)
      if (existing) {
        await this.remove(item.paper_id)
        return
      }
      await this.add(item, 5)
    },
    async remove(paperId: string) {
      await this.init()
      const index = this.items.findIndex((favorite) => favorite.paper.paper_id === paperId)
      if (index === -1) return
      this.items.splice(index, 1)
      await deleteFavorite(paperId)
    },
    async setRating(paperId: string, rating: FavoriteRating) {
      if (!isFavoriteRating(rating)) return
      await this.init()
      const index = this.items.findIndex((favorite) => favorite.paper.paper_id === paperId)
      if (index === -1) return
      const existing = this.items[index]!
      if (existing.rating === rating) return
      const next: FavoriteRecord = {
        ...existing,
        rating,
        updatedAt: Date.now(),
      }
      this.items.splice(index, 1, next)
      await saveFavorite(next)
    },
    async merge(records: FavoriteRecord[]) {
      await this.init()
      const current = new Map(this.items.map((record) => [record.paper.paper_id, record]))
      const changed: FavoriteRecord[] = []
      for (const record of uniqueRecords(records)) {
        const existing = current.get(record.paper.paper_id)
        if (!existing || record.updatedAt > existing.updatedAt) {
          current.set(record.paper.paper_id, record)
          changed.push(record)
        }
      }
      if (changed.length === 0) return 0
      this.items = [...current.values()]
      for (const record of changed) {
        await saveFavorite(record)
      }
      return changed.length
    },
    async replace(records: FavoriteRecord[]) {
      await this.init()
      const nextRecords = uniqueRecords(records)
      this.items = nextRecords
      await clearFavorites()
      for (const record of nextRecords) {
        await saveFavorite(record)
      }
      return nextRecords.length
    },
  },
})
