import { defineStore } from 'pinia'
import { buildUrl, fetchJson } from '@/lib/api'
import { useUiStore } from '@/stores/ui'

export const useRuntimeConfigStore = defineStore('runtimeConfig', {
  state: () => ({
    staticBaseUrl: '',
    loaded: false,
    loading: false,
  }),
  actions: {
    async load() {
      if (this.loaded || this.loading) return
      const ui = useUiStore()
      this.loading = true
      try {
        const data = await fetchJson(buildUrl('/config'), {
          timeoutMs: 10_000,
        })
        const config = data as { static_base_url?: string }
        this.staticBaseUrl = (config.static_base_url || '').replace(/\/+$/, '')
        this.loaded = true
      } catch {
        this.loaded = true
        ui.pushToast('Failed to load runtime config; using defaults', 'error')
      } finally {
        this.loading = false
      }
    },
  },
})
