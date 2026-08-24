<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AdminPipelineError,
  bindPipelineBibtex,
  getPipelineJob,
  publishPipelineJob,
  rejectPipelineJob,
  retryPipelineJob,
  type PipelineJob,
} from '@/lib/admin-pipeline'
import {
  formatPipelineBytes,
  isPipelineWorkerUnavailable,
  useAdminPipelineStore,
} from '@/stores/admin-pipeline'
import { useProtectedPipelinePreviews } from '@/composables/useProtectedPipelinePreviews'

const route = useRoute()
const router = useRouter()
const admin = useAdminPipelineStore()
const preview = useProtectedPipelinePreviews()
const job = ref<PipelineJob | null>(null)
const loading = ref(true)
const actionLoading = ref(false)
const errorMessage = ref('')
const staleRevision = ref(false)
const conflictRefreshing = ref(false)
const conflictRefreshFailed = ref(false)
const selectedBib = ref('')
const selectedModels = ref({ ocr: '', extract: '', translate: '' })
let routeGeneration = 0

const config = computed(() => admin.config)
const workerOffline = computed(() => isPipelineWorkerUnavailable(admin.worker))
const canBindBib = computed(() => job.value?.status === 'needs_attention' || job.value?.status === 'review_ready')
const canPublish = computed(() => job.value?.status === 'review_ready' && !conflictRefreshing.value && !conflictRefreshFailed.value)
const canRetry = computed(() => Boolean(job.value && ['failed', 'needs_attention', 'review_ready', 'published_with_warning'].includes(job.value.status) && !conflictRefreshing.value && !conflictRefreshFailed.value))
const candidates = computed(() => job.value?.bibtex.candidates ?? [])
const isIndexingRetry = computed(() => job.value?.status === 'published_with_warning')
const pdfObjectUrl = computed(() => preview.pdfUrl.value)

function modelOptions(name: 'ocr' | 'extract' | 'translate'): string[] {
  return config.value?.models[name].allowlist ?? []
}

function displayError(error: unknown, fallback: string): string {
  if (error instanceof AdminPipelineError && error.status === 401) {
    handleAuthLoss()
    return 'Admin token expired. Please sign in again.'
  }
  if (error instanceof AdminPipelineError && error.status === 409) {
    staleRevision.value = true
    return 'This job changed elsewhere. Refresh before trying the action again.'
  }
  return error instanceof Error && error.message ? error.message : fallback
}

function handleAuthLoss(): void {
  admin.logout()
  void router.replace('/admin/pipeline')
}

async function ensureAuth(): Promise<boolean> {
  if (admin.authenticated) return true
  if (admin.token) await admin.restore()
  if (!admin.authenticated) {
    await router.replace('/admin/pipeline')
    return false
  }
  return true
}

function setJob(next: PipelineJob): void {
  job.value = next
  selectedBib.value = next.bibtex.entry_key ?? '__none__'
  selectedModels.value = {
    ocr: next.selected_models.ocr ?? config.value?.models.ocr.default ?? '',
    extract: next.selected_models.extract ?? config.value?.models.extract.default ?? '',
    translate: next.selected_models.translate ?? config.value?.models.translate.default ?? '',
  }
}

async function loadJob(generation = routeGeneration): Promise<void> {
  const jobId = String(route.params.jobId || '')
  if (!admin.token || !jobId) return
  loading.value = true
  try {
    const result = await getPipelineJob(admin.token, jobId)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
    if (result.worker) admin.config = admin.config ? { ...admin.config, worker: result.worker } : admin.config
    staleRevision.value = false
    conflictRefreshing.value = false
    conflictRefreshFailed.value = false
    await preview.load(result.job.id, admin.token)
  } catch (error) {
    if (generation === routeGeneration && jobId === String(route.params.jobId || '')) {
      errorMessage.value = displayError(error, 'Job could not be loaded.')
    }
  } finally {
    if (generation === routeGeneration && jobId === String(route.params.jobId || '')) loading.value = false
  }
}

async function refreshJobAfterConflict(generation: number, jobId: string): Promise<void> {
  conflictRefreshing.value = true
  conflictRefreshFailed.value = false
  preview.dispose()
  const token = admin.token
  if (!token) {
    handleAuthLoss()
    conflictRefreshFailed.value = true
    conflictRefreshing.value = false
    return
  }
  let refreshed = false
  try {
    const result = await getPipelineJob(token, jobId)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
    if (result.worker) admin.config = admin.config ? { ...admin.config, worker: result.worker } : admin.config
    if (!admin.token || !admin.authenticated) {
      handleAuthLoss()
      return
    }
    await preview.load(result.job.id, token)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    if (preview.error.value) {
      errorMessage.value = 'Current job previews could not be refreshed.'
    } else {
      refreshed = true
    }
  } catch (error) {
    if (generation === routeGeneration && jobId === String(route.params.jobId || '')) {
      errorMessage.value = displayError(error, 'Current job could not be refreshed.')
    }
  } finally {
    if (generation === routeGeneration && jobId === String(route.params.jobId || '')) {
      conflictRefreshFailed.value = !refreshed
      conflictRefreshing.value = false
    }
  }
}

async function loadForRoute(): Promise<void> {
  const generation = ++routeGeneration
  job.value = null
  loading.value = true
  actionLoading.value = false
  errorMessage.value = ''
  staleRevision.value = false
  conflictRefreshing.value = false
  conflictRefreshFailed.value = false
  selectedBib.value = ''
  selectedModels.value = { ocr: '', extract: '', translate: '' }
  preview.dispose()
  if (!(await ensureAuth()) || generation !== routeGeneration) {
    if (generation === routeGeneration) loading.value = false
    return
  }
  await loadJob(generation)
}

async function bindBibtex(): Promise<void> {
  if (!admin.token || !job.value || !canBindBib.value || conflictRefreshing.value || conflictRefreshFailed.value) return
  const generation = routeGeneration
  const jobId = job.value.id
  actionLoading.value = true
  errorMessage.value = ''
  try {
    const result = await bindPipelineBibtex(admin.token, job.value.id, selectedBib.value === '__none__' ? null : selectedBib.value || null)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
    await preview.load(result.job.id, admin.token)
  } catch (error) {
    errorMessage.value = displayError(error, 'BibTeX binding failed.')
    if (error instanceof AdminPipelineError && error.status === 409) await refreshJobAfterConflict(generation, jobId)
  } finally {
    actionLoading.value = false
  }
}

async function retry(): Promise<void> {
  if (!admin.token || !job.value || !canRetry.value) return
  const generation = routeGeneration
  const jobId = job.value.id
  const expectedRevision = job.value.revision
  actionLoading.value = true
  errorMessage.value = ''
  try {
    const result = await retryPipelineJob(
      admin.token,
      jobId,
      isIndexingRetry.value ? undefined : selectedModels.value,
      isIndexingRetry.value ? expectedRevision : undefined,
    )
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
    staleRevision.value = false
    conflictRefreshFailed.value = false
  } catch (error) {
    errorMessage.value = displayError(error, 'Retry could not be queued.')
    if (error instanceof AdminPipelineError && error.status === 409) await refreshJobAfterConflict(generation, jobId)
  } finally {
    actionLoading.value = false
  }
}

async function reject(): Promise<void> {
  if (!admin.token || !job.value) return
  const generation = routeGeneration
  const jobId = job.value.id
  actionLoading.value = true
  errorMessage.value = ''
  try {
    const result = await rejectPipelineJob(admin.token, job.value.id)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
  } catch (error) {
    errorMessage.value = displayError(error, 'Job could not be rejected.')
    if (error instanceof AdminPipelineError && error.status === 409) await refreshJobAfterConflict(generation, jobId)
  } finally {
    actionLoading.value = false
  }
}

async function publish(): Promise<void> {
  if (!admin.token || !job.value || !canPublish.value) return
  const generation = routeGeneration
  const jobId = job.value.id
  const expectedRevision = job.value.revision
  actionLoading.value = true
  errorMessage.value = ''
  try {
    const result = await publishPipelineJob(admin.token, jobId, expectedRevision)
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    setJob(result.job)
    staleRevision.value = false
    conflictRefreshFailed.value = false
  } catch (error) {
    if (generation !== routeGeneration || jobId !== String(route.params.jobId || '')) return
    errorMessage.value = displayError(error, 'Publish could not be queued.')
    if (error instanceof AdminPipelineError && error.status === 409) {
      await refreshJobAfterConflict(generation, jobId)
    }
  } finally {
    if (generation === routeGeneration && jobId === String(route.params.jobId || '')) actionLoading.value = false
  }
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

onMounted(() => {
  void loadForRoute()
})

watch(() => String(route.params.jobId || ''), () => {
  void loadForRoute()
})
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 pb-12" data-testid="admin-job-page">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <button class="text-sm text-muted-foreground hover:text-foreground" type="button" @click="job?.batch_id ? router.push(`/admin/pipeline/batches/${encodeURIComponent(job.batch_id)}`) : router.push('/admin/pipeline')">← Back to batch</button>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight">Review paper</h1>
        <p class="mt-1 font-mono text-xs text-muted-foreground">{{ job?.filename || route.params.jobId }}</p>
      </div>
      <div v-if="job" class="flex flex-wrap items-center gap-2">
        <span class="rounded-full bg-muted px-3 py-1 text-xs font-medium capitalize">{{ statusLabel(job.status) }}</span>
        <span class="text-xs text-muted-foreground">revision {{ job.revision }}</span>
      </div>
    </div>

    <div v-if="workerOffline" data-testid="worker-offline" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status">Worker {{ admin.worker?.status }}. New processing/retry work may wait.</div>
    <div v-if="staleRevision" data-testid="stale-revision" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="alert">Stale revision. Current job state was returned; review it before retrying.</div>
    <p v-if="errorMessage" class="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{{ errorMessage }}</p>

    <div v-if="loading" class="rounded-xl border border-border/60 p-8 text-sm text-muted-foreground" role="status">Loading job…</div>
    <div v-else-if="!job" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">Job not found.</div>
    <template v-else>
      <section class="rounded-2xl border border-border/60 bg-card p-5 shadow-card" aria-labelledby="job-meta-title">
        <div class="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 id="job-meta-title" class="text-xl font-semibold">{{ job.filename || 'Untitled PDF' }}</h2>
            <p class="mt-1 text-sm text-muted-foreground">{{ formatPipelineBytes(job.size) }} · {{ job.progress.completed_steps }}/{{ job.progress.total_steps }} steps complete</p>
          </div>
          <div class="flex flex-wrap gap-2">
            <button data-testid="job-publish" class="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50" type="button" :disabled="!canPublish || actionLoading" @click="publish">Publish</button>
            <button data-testid="job-retry" class="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:cursor-not-allowed disabled:opacity-50" type="button" :disabled="!canRetry || actionLoading" @click="retry">{{ isIndexingRetry ? 'Retry indexing' : 'Retry' }}</button>
            <button data-testid="job-reject" class="rounded-md border border-destructive/50 px-3 py-2 text-sm text-destructive hover:bg-destructive/5 disabled:opacity-50" type="button" :disabled="actionLoading || conflictRefreshing || conflictRefreshFailed || ['published', 'published_with_warning', 'rejected'].includes(job.status)" @click="reject">Reject</button>
          </div>
        </div>
        <p v-if="job.failed_step" class="mt-4 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">Failed step: {{ job.failed_step }}<span v-if="job.error"> — {{ job.error }}</span></p>
      </section>

      <section v-if="canBindBib" class="rounded-2xl border border-border/60 bg-card p-5 shadow-card" aria-labelledby="bibtex-title">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div><h2 id="bibtex-title" class="text-lg font-semibold">BibTeX match</h2><p class="mt-1 text-sm text-muted-foreground">{{ job.bibtex.status === 'needs_attention' ? 'Choose candidate or explicitly mark no BibTeX.' : 'You can change pairing before publishing.' }}</p></div>
          <span class="rounded-full bg-muted px-2.5 py-1 text-xs">{{ job.bibtex.status }}</span>
        </div>
        <div class="mt-4 grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
          <div class="space-y-2">
            <label class="text-sm font-medium" for="bibtex-match">BibTeX entry</label>
            <select id="bibtex-match" data-testid="bibtex-match" v-model="selectedBib" class="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
              <option value="__none__">No BibTeX</option>
              <option v-for="candidate in candidates" :key="candidate.key" :value="candidate.key">{{ candidate.key }}{{ candidate.title ? ` — ${candidate.title}` : '' }}</option>
            </select>
            <p v-if="job.bibtex.diagnostics.reason" class="text-xs text-muted-foreground">Reason: {{ job.bibtex.diagnostics.reason }}<span v-if="job.bibtex.diagnostics.candidate_keys?.length"> · candidates {{ job.bibtex.diagnostics.candidate_keys.join(', ') }}</span></p>
          </div>
            <button data-testid="bibtex-bind" class="h-10 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50" type="button" :disabled="actionLoading || conflictRefreshing || conflictRefreshFailed" @click="bindBibtex">Save pairing</button>
        </div>
      </section>

      <section v-if="!isIndexingRetry" class="rounded-2xl border border-border/60 bg-card p-5 shadow-card" aria-labelledby="retry-models-title">
        <div><h2 id="retry-models-title" class="text-lg font-semibold">Retry models</h2><p class="mt-1 text-sm text-muted-foreground">Only allowlisted OCR, Extract, and Translate models are available.</p></div>
        <div class="mt-4 grid gap-4 md:grid-cols-3">
          <div v-for="name in ['ocr', 'extract', 'translate']" :key="name" class="space-y-2">
            <label class="text-sm font-medium capitalize" :for="`retry-${name}`">{{ name }} model</label>
            <select :id="`retry-${name}`" v-model="selectedModels[name as 'ocr' | 'extract' | 'translate']" class="h-10 w-full rounded-md border border-input bg-background px-3 text-sm">
              <option v-for="option in modelOptions(name as 'ocr' | 'extract' | 'translate')" :key="option" :value="option">{{ option }}</option>
            </select>
          </div>
        </div>
      </section>

      <section class="space-y-3" aria-labelledby="preview-title">
        <div class="flex flex-wrap items-end justify-between gap-3"><div><h2 id="preview-title" class="text-xl font-semibold">Read-only previews</h2><p class="mt-1 text-sm text-muted-foreground">Protected artifacts are fetched with Admin auth and remain outside public static paths.</p></div><span v-if="preview.loading" class="text-xs text-muted-foreground" role="status">Loading previews…</span></div>
        <p v-if="preview.error" class="rounded-xl border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="alert">{{ preview.error }}</p>
        <div class="grid gap-4 lg:grid-cols-2">
          <article class="rounded-xl border border-border/60 bg-card p-4"><h3 class="text-sm font-semibold">PDF</h3><div class="mt-3 min-h-[20rem] overflow-hidden rounded-lg bg-muted/30"><iframe v-if="pdfObjectUrl" :src="pdfObjectUrl" title="Protected paper PDF preview" class="h-[32rem] w-full border-0" /> <p v-else class="p-6 text-sm text-muted-foreground">PDF preview unavailable.</p></div></article>
          <article class="rounded-xl border border-border/60 bg-card p-4"><h3 class="text-sm font-semibold">Source Markdown</h3><pre class="mt-3 max-h-[32rem] min-h-[20rem] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/30 p-4 text-xs leading-6">{{ preview.sourceMarkdown || 'Source Markdown unavailable.' }}</pre></article>
          <article class="rounded-xl border border-border/60 bg-card p-4"><h3 class="text-sm font-semibold">Summary JSON</h3><pre class="mt-3 max-h-[28rem] min-h-[14rem] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/30 p-4 font-mono text-xs leading-6">{{ preview.summaryJson || 'Summary JSON unavailable.' }}</pre></article>
          <article class="rounded-xl border border-border/60 bg-card p-4"><h3 class="text-sm font-semibold">Translated Markdown</h3><pre class="mt-3 max-h-[32rem] min-h-[20rem] overflow-auto whitespace-pre-wrap rounded-lg bg-muted/30 p-4 text-xs leading-6">{{ preview.translatedMarkdown || 'Translated Markdown unavailable.' }}</pre></article>
        </div>
      </section>
    </template>
  </div>
</template>
