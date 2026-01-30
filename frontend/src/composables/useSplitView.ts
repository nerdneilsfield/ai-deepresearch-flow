import { ref } from 'vue'
import { useStorage } from '@vueuse/core'

export type ViewMode = 'summary' | 'split' | 'markdown' | 'pdf'

const memoryStorage = (() => {
  const store = new Map<string, string>()
  return {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => {
      store.set(key, value)
    },
    removeItem: (key: string) => {
      store.delete(key)
    },
    clear: () => {
      store.clear()
    },
    key: (index: number) => Array.from(store.keys())[index] ?? null,
    get length() {
      return store.size
    },
  } as Storage
})()

export function useSplitView() {
  const viewMode = ref<ViewMode>('summary')
  const contentTab = ref('summary')
  const leftView = ref('summary')
  const rightView = ref('summary')
  const storage =
    typeof window !== 'undefined' &&
    window.localStorage &&
    typeof window.localStorage.getItem === 'function'
      ? window.localStorage
      : memoryStorage
  const splitPercent = useStorage('paperdb-split-percent', 55, storage)
  const detailWidthPercent = useStorage('paperdb-detail-width', 90, storage)

  function swapSplit() {
    const current = leftView.value
    leftView.value = rightView.value
    rightView.value = current
  }

  function widenLeft() {
    splitPercent.value = Math.min(70, splitPercent.value + 5)
  }

  function tightenLeft() {
    splitPercent.value = Math.max(30, splitPercent.value - 5)
  }

  return {
    viewMode,
    contentTab,
    leftView,
    rightView,
    splitPercent,
    detailWidthPercent,
    swapSplit,
    widenLeft,
    tightenLeft,
  }
}
