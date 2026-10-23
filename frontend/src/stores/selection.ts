import { defineStore } from 'pinia'
import { MAX_BATCH_SIZE } from '@/lib/config'
import {
  loadAllItems,
  saveItem,
  deleteItem,
  clearAll,
  loadSelectionListsMeta,
  saveSelectionListsMeta,
  loadSelectionListItems,
  saveSelectionListItems,
  deleteSelectionListItems,
} from '@/lib/selection-db'
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
    lists: [{ id: 'default', name: 'Default' }] as { id: string; name: string }[],
    activeListId: 'default',
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
      const meta = await loadSelectionListsMeta()
      if (meta && meta.lists.some((list) => list.id === meta.activeId)) {
        this.lists = meta.lists
        this.activeListId = meta.activeId
      }
      this._initialized = true
    },
    async _saveListsMeta() {
      await saveSelectionListsMeta({ lists: this.lists.map((list) => ({ ...list })), activeId: this.activeListId })
    },
    async switchList(id: string) {
      await this.init()
      if (id === this.activeListId || !this.lists.some((list) => list.id === id)) return
      await saveSelectionListItems(this.activeListId, [...this.items])
      const next = await loadSelectionListItems(id)
      await this.replace(next)
      await deleteSelectionListItems(id)
      this.activeListId = id
      await this._saveListsMeta()
    },
    async createList(name: string) {
      await this.init()
      const id = `list-${Date.now().toString(36)}`
      this.lists.push({ id, name })
      await saveSelectionListItems(id, [])
      await this.switchList(id)
    },
    async renameList(id: string, name: string) {
      await this.init()
      const list = this.lists.find((entry) => entry.id === id)
      if (!list) return
      list.name = name
      await this._saveListsMeta()
    },
    async deleteList(id: string) {
      await this.init()
      if (this.lists.length <= 1) return
      if (id === this.activeListId) {
        await this.switchList(this.lists.find((list) => list.id !== id)!.id)
      }
      this.lists = this.lists.filter((list) => list.id !== id)
      await deleteSelectionListItems(id)
      await this._saveListsMeta()
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
