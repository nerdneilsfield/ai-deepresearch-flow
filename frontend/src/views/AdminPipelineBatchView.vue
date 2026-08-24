<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AdminPipelineError,
  cancelPipelineBatch,
  getPipelineBatch,
  notifyPipelineTransition,
  publishReadyPipelineBatch,
  requestPipelineNotifications,
  type BatchPublishOutcome,
  type PipelineBatch,
} from '@/lib/admin-pipeline'
import {
  formatPipelineBytes,
  isPipelineJobTerminal,
  isPipelineWorkerUnavailable,
  useAdminPipelineStore,
} from '@/stores/admin-pipeline'

const route = useRoute()
const router = useRouter()
const admin = useAdminPipelineStore()
const batch = ref<PipelineBatch | null>(null)
const loading = ref(true)
const actionLoading = ref(false)
const errorMessage = ref('')
const outcomes = ref<BatchPublishOutcome[]>([])
const notificationsEnabled = ref(false)
const previousStatuses = new Map<string, string>()
let pollTimer: ReturnType<typeof setInterval> | null = null
let pollInFlight = false

const workerOffline = computed(() => isPipelineWorkerUnavailable(admin.worker))
const readyJobs = computed(() => batch.value?.jobs.filter((job) => job.status === 'review_ready') ?? [])
const activeJobs = computed(() => batch.value?.jobs.filter((job) => !isPipelineJobTerminal(job)) ?? [])
const isPolling = computed(() => activeJobs.value.length > 0)

function displayError(error: unknown, fallback: string): string {
  if (error instanceof AdminPipelineError && error.status === 401) {
    admin.logout()
    void router.replace('/admin/pipeline')
    return 'Admin token expired. Please sign in again.'
  }
  if (error instanceof AdminPipelineError && error.status === 409) return `Conflict: ${error.message}`
  return error instanceof Error && error.message ? error.message : fallback
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

function stopPolling(): void {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function startPolling(): void {
  stopPolling()
  if (!isPolling.value) return
  pollTimer = setInterval(() => {
    void refresh()
  }, 3000)
}

async function refresh(): Promise<void> {
  if (!admin.token || !route.params.batchId || pollInFlight) return
  pollInFlight = true
  try {
    const result = await getPipelineBatch(admin.token, String(route.params.batchId))
    for (const nextJob of result.batch.jobs) {
      const previous = previousStatuses.get(nextJob.id)
      if (notificationsEnabled.value && previous && previous !== nextJob.status && ['published', 'published_with_warning', 'failed'].includes(nextJob.status)) {
        notifyPipelineTransition(
          nextJob.status === 'failed' ? 'Pipeline job failed' : 'Pipeline job completed',
          `${nextJob.filename || nextJob.id}: ${statusLabel(nextJob.status)}`,
        )
      }
      previousStatuses.set(nextJob.id, nextJob.status)
    }
    batch.value = result.batch
    await admin.refreshConfig()
    errorMessage.value = ''
    if (!isPolling.value) stopPolling()
    else if (!pollTimer) startPolling()
  } catch (error) {
    errorMessage.value = displayError(error, 'Batch could not be loaded.')
  } finally {
    loading.value = false
    pollInFlight = false
  }
}

async function enableNotifications(): Promise<void> {
  notificationsEnabled.value = await requestPipelineNotifications()
}

async function publishReady(): Promise<void> {
  if (!admin.token || !batch.value || readyJobs.value.length === 0) return
  actionLoading.value = true
  errorMessage.value = ''
  outcomes.value = []
  try {
    const result = await publishReadyPipelineBatch(
      admin.token,
      batch.value.id,
      readyJobs.value.map((job) => ({ job_id: job.id, expected_revision: job.revision })),
    )
    outcomes.value = result.outcomes
    await refresh()
  } catch (error) {
    errorMessage.value = displayError(error, 'Ready jobs could not be queued.')
  } finally {
    actionLoading.value = false
  }
}

async function cancelBatch(): Promise<void> {
  if (!admin.token || !batch.value) return
  actionLoading.value = true
  errorMessage.value = ''
  try {
    const result = await cancelPipelineBatch(admin.token, batch.value.id)
    outcomes.value = result.outcomes
    await refresh()
  } catch (error) {
    errorMessage.value = displayError(error, 'Batch cancellation failed.')
  } finally {
    actionLoading.value = false
  }
}

function openJob(jobId: string): void {
  void router.push(`/admin/pipeline/jobs/${encodeURIComponent(jobId)}`)
}

function statusLabel(status: string): string {
  return status.replace(/_/g, ' ')
}

onMounted(async () => {
  if (await ensureAuth()) {
    await refresh()
    startPolling()
  }
})

onUnmounted(stopPolling)
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 pb-12" data-testid="admin-batch-page">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <button class="text-sm text-muted-foreground hover:text-foreground" type="button" @click="router.push('/admin/pipeline')">← Admin pipeline</button>
        <h1 class="mt-2 text-3xl font-semibold tracking-tight">Batch review</h1>
        <p class="mt-1 font-mono text-xs text-muted-foreground">{{ route.params.batchId }}</p>
      </div>
      <div class="flex flex-wrap gap-2">
        <button data-testid="enable-notifications" class="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50" type="button" :disabled="notificationsEnabled" @click="enableNotifications">{{ notificationsEnabled ? 'Notifications enabled' : 'Enable notifications' }}</button>
        <button class="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50" type="button" :disabled="loading || actionLoading" @click="refresh">Refresh</button>
        <button data-testid="batch-publish-ready" class="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50" type="button" :disabled="readyJobs.length === 0 || actionLoading" @click="publishReady">Publish ready ({{ readyJobs.length }})</button>
        <button class="rounded-md border border-destructive/50 px-3 py-2 text-sm text-destructive hover:bg-destructive/5 disabled:opacity-50" type="button" :disabled="actionLoading || !activeJobs.length" @click="cancelBatch">Cancel remaining</button>
      </div>
    </div>

    <div v-if="workerOffline" data-testid="worker-offline" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status" aria-live="polite">
      Worker {{ admin.worker?.status }}. Processing may be paused; this page will keep checking status.
    </div>
    <p v-if="errorMessage" class="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{{ errorMessage }}</p>
    <div v-if="outcomes.length" class="rounded-xl border border-border/60 bg-muted/20 p-4" data-testid="batch-outcomes" aria-live="polite">
      <h2 class="text-sm font-semibold">Publish outcomes</h2>
      <ul class="mt-2 grid gap-1 text-sm">
        <li v-for="outcome in outcomes" :key="`${outcome.job_id}-${outcome.status}`" class="flex flex-wrap gap-2">
          <span class="font-mono text-xs">{{ outcome.job_id }}</span>
          <span>{{ outcome.status }}</span>
          <span v-if="outcome.error" class="text-destructive">{{ outcome.error.message || outcome.error.code }}</span>
        </li>
      </ul>
    </div>

    <div v-if="loading" class="rounded-xl border border-border/60 p-8 text-sm text-muted-foreground" role="status">Loading batch…</div>
    <div v-else-if="!batch" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">Batch not found.</div>
    <template v-else>
      <section class="grid gap-3 sm:grid-cols-3" aria-label="Batch summary">
        <div class="rounded-xl border border-border/60 bg-card p-4"><div class="text-xs text-muted-foreground">Jobs</div><div class="mt-1 text-2xl font-semibold">{{ batch.job_count }}</div></div>
        <div class="rounded-xl border border-border/60 bg-card p-4"><div class="text-xs text-muted-foreground">Active</div><div class="mt-1 text-2xl font-semibold">{{ activeJobs.length }}</div></div>
        <div class="rounded-xl border border-border/60 bg-card p-4"><div class="text-xs text-muted-foreground">Ready to publish</div><div class="mt-1 text-2xl font-semibold">{{ readyJobs.length }}</div></div>
      </section>

      <section class="space-y-3" aria-labelledby="batch-jobs-title">
        <div class="flex items-center justify-between gap-3"><h2 id="batch-jobs-title" class="text-xl font-semibold">Papers</h2><span v-if="isPolling" class="text-xs text-muted-foreground" role="status">Updating every 3 seconds</span></div>
        <div v-if="batch.jobs.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No jobs in this batch.</div>
        <div v-else class="grid gap-3">
          <article v-for="job in batch.jobs" :key="job.id" class="rounded-xl border border-border/60 bg-card p-4 shadow-sm">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <button class="min-w-0 text-left" type="button" @click="openJob(job.id)">
                <span class="block truncate font-medium hover:text-primary">{{ job.filename || job.id }}</span>
                <span class="mt-1 block text-xs text-muted-foreground">{{ formatPipelineBytes(job.size) }} · revision {{ job.revision }}</span>
              </button>
              <span class="rounded-full bg-muted px-2.5 py-1 text-xs font-medium capitalize" :data-status="job.status">{{ statusLabel(job.status) }}</span>
            </div>
            <div class="mt-3 h-2 overflow-hidden rounded-full bg-muted" role="progressbar" :aria-valuenow="job.progress.completed_steps" :aria-valuemin="0" :aria-valuemax="job.progress.total_steps" :aria-label="`${job.filename || job.id} processing progress`">
              <div class="h-full rounded-full bg-primary transition-all" :style="{ width: `${job.progress.total_steps ? Math.round(job.progress.completed_steps / job.progress.total_steps * 100) : 0}%` }" />
            </div>
            <div class="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>{{ job.progress.completed_steps }}/{{ job.progress.total_steps }} steps</span>
              <span>BibTeX: {{ job.bibtex.status }}</span>
              <span v-if="job.failed_step" class="text-destructive">Failed: {{ job.failed_step }}{{ job.error ? ` — ${job.error}` : '' }}</span>
              <span v-if="job.bibtex.status === 'needs_attention'" class="text-amber-700 dark:text-amber-300">Candidates: {{ job.bibtex.candidates.map((candidate) => candidate.key).join(', ') || 'none' }}</span>
            </div>
            <div class="mt-3 flex justify-end"><button class="text-sm font-medium text-primary hover:underline" type="button" @click="openJob(job.id)">{{ job.status === 'review_ready' || job.status === 'needs_attention' ? 'Review' : 'Open details' }} →</button></div>
          </article>
        </div>
      </section>
    </template>
  </div>
</template>
