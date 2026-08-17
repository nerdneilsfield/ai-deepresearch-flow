import { defineStore } from 'pinia'
import { MAX_BATCH_SIZE } from '@/lib/config'
import { loadAllItems, saveItem, deleteItem, clearAll } from '@/lib/selection-db'
import type { SearchItem } from '@/types/api'

// Fallback to localStorage for simple count tracking
const META_KEY = 'deepresearch_selection_meta'

function saveMeta(count: number) {
  try {
    localStorage.setItem(META_KEY, JSON.stringify({ count, updated: Date.now() }))
  } catch {
    // Ignore
  }
}

function uniqueItems(items: SearchItem[]) {
  const seen = new Set<string>()
  return items.filter((item) => {
    if (seen.has(item.paper_id)) return false
    seen.add(item.paper_id)
    return true
  })
}

export const useSelectionStore = defineStore('selection', {
  state: () => ({ 
    items: [] as SearchItem[],
    _initialized: false 
  }),
  getters: {
    count: (state) => state.items.length,
    isFull: (state) => state.items.length >= MAX_BATCH_SIZE,
    selectedIds: (state) => new Set(state.items.map(i => i.paper_id)),
    selected: (state) => state.items
  },
  actions: {
    async init() {
      if (this._initialized) return
      const loaded = await loadAllItems()
      this.items = loaded.slice(0, MAX_BATCH_SIZE) as SearchItem[]
      this._initialized = true
    },
    async toggle(item: SearchItem) {
      await this.init()
      const idx = this.items.findIndex((i) => i.paper_id === item.paper_id)
      if (idx !== -1) {
        this.items.splice(idx, 1)
        await deleteItem(item.paper_id)
      } else {
        if (this.items.length >= MAX_BATCH_SIZE) return
        this.items.push(item)
        await saveItem(item.paper_id, item)
      }
      saveMeta(this.items.length)
    },
    async add(item: SearchItem) {
      await this.merge([item])
    },
    async merge(items: SearchItem[]) {
      await this.init()
      const selectedIds = new Set(this.items.map((item) => item.paper_id))
      const available = Math.max(0, MAX_BATCH_SIZE - this.items.length)
      const additions = uniqueItems(items)
        .filter((item) => !selectedIds.has(item.paper_id))
        .slice(0, available)
      if (additions.length === 0) return 0
      this.items.push(...additions)
      for (const item of additions) {
        await saveItem(item.paper_id, item)
      }
      saveMeta(this.items.length)
      return additions.length
    },
    async replace(items: SearchItem[]) {
      await this.init()
      const nextItems = uniqueItems(items).slice(0, MAX_BATCH_SIZE)
      this.items = nextItems
      await clearAll()
      for (const item of nextItems) {
        await saveItem(item.paper_id, item)
      }
      saveMeta(this.items.length)
      return nextItems.length
    },
    async remove(id: string) {
      await this.init()
      const idx = this.items.findIndex((i) => i.paper_id === id)
      if (idx !== -1) {
        this.items.splice(idx, 1)
        await deleteItem(id)
        saveMeta(this.items.length)
      }
    },
    async clear() {
      this.items = []
      await clearAll()
      saveMeta(0)
    },
    getNextId(currentId: string): string | null {
      const idx = this.items.findIndex(i => i.paper_id === currentId)
      if (idx === -1 || idx >= this.items.length - 1) return null
      const next = this.items[idx + 1]!
      return next.paper_id
    },
    getPrevId(currentId: string): string | null {
      const idx = this.items.findIndex(i => i.paper_id === currentId)
      if (idx <= 0) return null
      const prev = this.items[idx - 1]!
      return prev.paper_id
    }
  },
})
