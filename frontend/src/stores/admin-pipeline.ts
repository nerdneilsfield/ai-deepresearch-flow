import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import {
  AdminPipelineError,
  clearAdminToken,
  fetchAdminConfig,
  getAdminToken,
  setAdminToken,
  type PipelineConfig,
  type PipelineJob,
  type PipelineWorkerStatus,
} from '@/lib/admin-pipeline'

export const PIPELINE_TERMINAL_STATUSES = new Set([
  'failed',
  'needs_attention',
  'review_ready',
  'rejected',
  'cancelled',
  'published',
  'published_with_warning',
])

export const PIPELINE_WORKER_ACTIVE_STATUSES = new Set([
  'queued',
  'running',
  'publish_queued',
  'publishing',
  'indexing',
])

export function isPipelineJobTerminal(job: Pick<PipelineJob, 'status'>): boolean {
  return PIPELINE_TERMINAL_STATUSES.has(job.status)
}

export function isPipelineWorkerUnavailable(worker?: PipelineWorkerStatus | null): boolean {
  return worker?.status === 'offline' || worker?.status === 'degraded'
}

export function formatPipelineBytes(value: number | null | undefined): string {
  if (!Number.isFinite(value) || !value || value < 0) return '0 B'
  const units = ['B', 'KiB', 'MiB', 'GiB']
  let size = value
  let unit = 0
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024
    unit += 1
  }
  return `${size >= 10 || unit === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unit]}`
}

export const useAdminPipelineStore = defineStore('admin-pipeline', () => {
  const token = ref<string | null>(getAdminToken())
  const config = ref<PipelineConfig | null>(null)
  const authLoading = ref(false)
  const authError = ref('')
  const lastValidatedAt = ref<number | null>(null)

  const authenticated = computed(() => Boolean(token.value && config.value?.enabled))
  const worker = computed<PipelineWorkerStatus | null>(() => config.value?.worker ?? null)

  function clearSession(): void {
    clearAdminToken()
    token.value = null
    config.value = null
    lastValidatedAt.value = null
  }

  async function validate(candidate: string): Promise<PipelineConfig> {
    const next = await fetchAdminConfig(candidate)
    if (!next.enabled) {
      throw new AdminPipelineError(404, 'disabled', 'Admin pipeline is disabled')
    }
    return next
  }

  async function login(candidate: string): Promise<boolean> {
    authLoading.value = true
    authError.value = ''
    const value = candidate.trim()
    if (!value) {
      authError.value = 'Admin token is required.'
      authLoading.value = false
      return false
    }
    setAdminToken(value)
    try {
      const next = await validate(value)
      token.value = value
      config.value = next
      lastValidatedAt.value = Date.now()
      return true
    } catch (error) {
      clearSession()
      authError.value = error instanceof Error ? error.message : 'Admin token could not be validated.'
      return false
    } finally {
      authLoading.value = false
    }
  }

  async function restore(): Promise<boolean> {
    const saved = getAdminToken()
    if (!saved) {
      clearSession()
      return false
    }
    authLoading.value = true
    authError.value = ''
    try {
      const next = await validate(saved)
      token.value = saved
      config.value = next
      lastValidatedAt.value = Date.now()
      return true
    } catch (error) {
      clearSession()
      authError.value = error instanceof Error ? error.message : 'Admin token could not be validated.'
      return false
    } finally {
      authLoading.value = false
    }
  }

  async function refreshConfig(): Promise<PipelineConfig | null> {
    if (!token.value) return null
    try {
      const next = await validate(token.value)
      config.value = next
      lastValidatedAt.value = Date.now()
      return next
    } catch (error) {
      if (error instanceof AdminPipelineError && (error.status === 401 || error.status === 404)) {
        clearSession()
      }
      return null
    }
  }

  function logout(): void {
    clearSession()
    authError.value = ''
  }

  return {
    token,
    config,
    authLoading,
    authError,
    lastValidatedAt,
    authenticated,
    worker,
    login,
    restore,
    refreshConfig,
    logout,
  }
})
