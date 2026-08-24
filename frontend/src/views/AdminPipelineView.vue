<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { AdminPipelineError, createPipelineBatch, listPipelineBatches, type PipelineBatch } from '@/lib/admin-pipeline'
import { formatPipelineBytes, useAdminPipelineStore, isPipelineWorkerUnavailable } from '@/stores/admin-pipeline'

const router = useRouter()
const admin = useAdminPipelineStore()
const tokenDraft = ref('')
const pdfFiles = ref<File[]>([])
const bibtexFile = ref<File | null>(null)
const selectedModels = ref({ ocr: '', extract: '', translate: '' })
const batches = ref<PipelineBatch[]>([])
const loadingBatches = ref(false)
const uploadLoading = ref(false)
const errorMessage = ref('')
const successMessage = ref('')
const inputElement = ref<HTMLInputElement | null>(null)
const bibInputElement = ref<HTMLInputElement | null>(null)
const pdfSelectionTouched = ref(false)
let authViewGeneration = 0
let initializedToken: string | null = null
let batchLoadPromise: Promise<void> | null = null
let batchLoadToken: string | null = null

const config = computed(() => admin.config)
const workerOffline = computed(() => isPipelineWorkerUnavailable(config.value?.worker))
const totalBytes = computed(() => pdfFiles.value.reduce((sum, file) => sum + file.size, 0))
const pdfCountInvalid = computed(() => Boolean(config.value && pdfFiles.value.length > config.value.limits.pdfs_per_batch))
const aggregateInvalid = computed(() => Boolean(config.value && totalBytes.value > config.value.limits.max_batch_bytes))
const sizeInvalid = computed(() => Boolean(config.value && pdfFiles.value.some((file) => file.size > config.value!.limits.max_pdf_bytes)))
const pdfTypeInvalid = computed(() => pdfFiles.value.some((file) => !file.name.toLowerCase().endsWith('.pdf')))
const emptyPdfInvalid = computed(() => pdfSelectionTouched.value && pdfFiles.value.length === 0)
const bibtexInvalid = computed(() => Boolean(config.value && bibtexFile.value && bibtexFile.value.size > config.value.limits.bibtex_max_bytes))
const bibtexTypeInvalid = computed(() => Boolean(bibtexFile.value && !bibtexFile.value.name.toLowerCase().endsWith('.bib')))
const pdfValidationError = computed(() => emptyPdfInvalid.value || pdfCountInvalid.value || aggregateInvalid.value || sizeInvalid.value || pdfTypeInvalid.value)
const bibtexValidationError = computed(() => bibtexInvalid.value || bibtexTypeInvalid.value)
const uploadInvalid = computed(() => pdfFiles.value.length === 0 || pdfCountInvalid.value || aggregateInvalid.value || sizeInvalid.value || pdfTypeInvalid.value || bibtexInvalid.value || bibtexTypeInvalid.value)
const hasUploadValidationError = computed(() => pdfValidationError.value || bibtexValidationError.value)

function modelDefault(name: 'ocr' | 'extract' | 'translate'): string {
  return config.value?.models[name].default ?? ''
}

function modelOptions(name: 'ocr' | 'extract' | 'translate'): string[] {
  return config.value?.models[name].allowlist ?? []
}

function applyDefaults(): void {
  selectedModels.value = {
    ocr: selectedModels.value.ocr || modelDefault('ocr'),
    extract: selectedModels.value.extract || modelDefault('extract'),
    translate: selectedModels.value.translate || modelDefault('translate'),
  }
}

function setPdfFiles(event: Event): void {
  pdfSelectionTouched.value = true
  const files = Array.from((event.target as HTMLInputElement).files ?? [])
  pdfFiles.value = files
  errorMessage.value = ''
}

function setBibtex(event: Event): void {
  bibtexFile.value = (event.target as HTMLInputElement).files?.[0] ?? null
  errorMessage.value = ''
}

function clearUpload(): void {
  pdfFiles.value = []
  bibtexFile.value = null
  pdfSelectionTouched.value = true
  if (inputElement.value) inputElement.value.value = ''
  if (bibInputElement.value) bibInputElement.value.value = ''
}

function displayError(error: unknown, fallback: string): string {
  if (error instanceof AdminPipelineError && error.status === 401) {
    admin.logout()
    return 'Admin token expired. Please sign in again.'
  }
  return error instanceof Error && error.message ? error.message : fallback
}

async function login(): Promise<void> {
  errorMessage.value = ''
  const valid = await admin.login(tokenDraft.value)
  if (!valid) errorMessage.value = admin.authError || 'Admin token could not be validated.'
  else {
    tokenDraft.value = ''
  }
}

async function loadBatches(token = admin.token, generation = authViewGeneration): Promise<void> {
  if (!token) return
  if (batchLoadPromise && batchLoadToken === token && generation === authViewGeneration) return batchLoadPromise
  loadingBatches.value = true
  batchLoadToken = token
  let request!: Promise<void>
  request = (async () => {
    try {
      const result = await listPipelineBatches(token, 1, 20)
      if (generation === authViewGeneration && admin.authenticated && admin.token === token) batches.value = result.items
    } catch (error) {
      if (generation === authViewGeneration && admin.authenticated && admin.token === token) {
        errorMessage.value = displayError(error, 'Batches could not be loaded.')
      }
    } finally {
      if (generation === authViewGeneration && admin.token === token) loadingBatches.value = false
      if (batchLoadPromise === request) {
        batchLoadPromise = null
        batchLoadToken = null
      }
    }
  })()
  batchLoadPromise = request
  return request
}

function refreshBatches(): void {
  void loadBatches()
}

async function upload(): Promise<void> {
  if (!admin.token || uploadInvalid.value) return
  uploadLoading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const result = await createPipelineBatch(
      { pdfs: pdfFiles.value, bibtex: bibtexFile.value, models: selectedModels.value },
      admin.token,
    )
    clearUpload()
    successMessage.value = `Batch ${result.batch_id} queued.`
    await router.push(`/admin/pipeline/batches/${encodeURIComponent(result.batch_id)}`)
  } catch (error) {
    errorMessage.value = displayError(error, 'Upload failed.')
  } finally {
    uploadLoading.value = false
  }
}

function logout(): void {
  admin.logout()
}

function openBatch(batchId: string): void {
  void router.push(`/admin/pipeline/batches/${encodeURIComponent(batchId)}`)
}

watch(
  [() => admin.authenticated, () => admin.config, () => admin.token],
  ([authenticated, nextConfig, token]) => {
    if (!authenticated || !nextConfig || !token) {
      if (initializedToken !== null || batches.value.length > 0) {
        initializedToken = null
        authViewGeneration += 1
        batches.value = []
        selectedModels.value = { ocr: '', extract: '', translate: '' }
        loadingBatches.value = false
      }
      return
    }
    if (initializedToken === token) {
      applyDefaults()
      return
    }
    initializedToken = token
    authViewGeneration += 1
    applyDefaults()
    void loadBatches(token, authViewGeneration)
  },
  { immediate: true },
)

onMounted(() => {
  if (admin.token && !admin.authenticated) void admin.restore()
})
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-6 pb-12" data-testid="admin-pipeline-page">
    <section v-if="!admin.authenticated" class="mx-auto max-w-xl rounded-2xl border border-border/60 bg-card p-6 shadow-card">
      <div class="space-y-2">
        <p class="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Administrator</p>
        <h1 class="text-2xl font-semibold tracking-tight">Pipeline sign in</h1>
        <p class="text-sm leading-6 text-muted-foreground">Enter Admin token to upload and review papers. Token remains in this browser session only.</p>
      </div>
      <form class="mt-6 space-y-4" @submit.prevent="login">
        <div class="space-y-2">
          <label class="text-sm font-medium" for="admin-token">Admin token</label>
          <input
            id="admin-token"
            v-model="tokenDraft"
            data-testid="admin-token-input"
            class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            type="password"
            autocomplete="off"
            spellcheck="false"
            required
          >
        </div>
        <p v-if="errorMessage || admin.authError" class="text-sm text-destructive" role="alert">{{ errorMessage || admin.authError }}</p>
        <button
          data-testid="admin-login"
          class="inline-flex h-10 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
          type="submit"
          :disabled="admin.authLoading"
        >
          {{ admin.authLoading ? 'Validating…' : 'Validate token' }}
        </button>
      </form>
    </section>

    <template v-else>
      <div class="flex flex-wrap items-start justify-between gap-4">
        <div>
          <p class="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Admin pipeline</p>
          <h1 class="mt-1 text-3xl font-semibold tracking-tight">Upload papers</h1>
          <p class="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">Process small PDF batches in background, then review protected previews before publishing.</p>
        </div>
        <button class="rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-muted" type="button" @click="logout">Sign out</button>
      </div>

      <div v-if="workerOffline" data-testid="worker-offline" class="rounded-xl border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200" role="status" aria-live="polite">
        Worker {{ config?.worker.status }}. Jobs remain queued until Worker heartbeat returns.
      </div>

      <p v-if="errorMessage" class="rounded-xl border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive" role="alert">{{ errorMessage }}</p>
      <p v-if="successMessage" class="rounded-xl border border-emerald-300 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/40 dark:text-emerald-200" role="status">{{ successMessage }}</p>

      <section class="rounded-2xl border border-border/60 bg-card p-5 shadow-card" aria-labelledby="upload-title">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 id="upload-title" class="text-xl font-semibold">New batch</h2>
            <p class="mt-1 text-sm text-muted-foreground">PDFs: {{ pdfFiles.length }}/{{ config?.limits.pdfs_per_batch }} · {{ formatPipelineBytes(totalBytes) }}/{{ formatPipelineBytes(config?.limits.max_batch_bytes) }}</p>
          </div>
          <button class="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted" type="button" @click="clearUpload">Clear</button>
        </div>

        <div class="mt-5 grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
          <div class="space-y-4">
            <div class="space-y-2">
              <label class="text-sm font-medium" for="pdf-files">PDF files</label>
              <input id="pdf-files" ref="inputElement" data-testid="pdf-input" :aria-describedby="pdfValidationError ? 'pdf-upload-error' : undefined" class="block w-full rounded-md border border-input bg-background p-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-primary-foreground" type="file" accept=".pdf,application/pdf" multiple @change="setPdfFiles">
              <p class="text-xs text-muted-foreground">Each PDF ≤ {{ formatPipelineBytes(config?.limits.max_pdf_bytes) }}. Batch ≤ {{ formatPipelineBytes(config?.limits.max_batch_bytes) }}.</p>
            </div>
            <div class="space-y-2">
              <label class="text-sm font-medium" for="bibtex-file">BibTeX file <span class="font-normal text-muted-foreground">(optional)</span></label>
              <input id="bibtex-file" ref="bibInputElement" data-testid="bibtex-input" :aria-describedby="bibtexValidationError ? 'bib-upload-error' : undefined" class="block w-full rounded-md border border-input bg-background p-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-muted file:px-3 file:py-1.5 file:text-sm file:font-medium" type="file" accept=".bib,application/x-bibtex,text/plain" @change="setBibtex">
              <p class="text-xs text-muted-foreground">Maximum {{ formatPipelineBytes(config?.limits.bibtex_max_bytes) }}. Ambiguous matches can be corrected after processing.</p>
            </div>
            <div v-if="pdfValidationError || bibtexValidationError" id="upload-validation-error" class="space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive" role="alert" aria-live="assertive" data-testid="upload-validation-error">
              <div v-if="pdfValidationError" id="pdf-upload-error">
                <p v-if="emptyPdfInvalid">Select at least one PDF before uploading.</p>
                <p v-if="pdfCountInvalid">Too many PDFs for one batch.</p>
                <p v-if="aggregateInvalid">Combined PDF size exceeds batch limit.</p>
                <p v-if="sizeInvalid">One or more PDFs exceed per-file limit.</p>
                <p v-if="pdfTypeInvalid">Only PDF files can be uploaded. Each filename must end in .pdf.</p>
              </div>
              <div v-if="bibtexValidationError" id="bib-upload-error">
                <p v-if="bibtexInvalid">BibTeX file exceeds configured limit.</p>
                <p v-if="bibtexTypeInvalid">BibTeX file must use a .bib extension.</p>
              </div>
            </div>
          </div>

          <div class="space-y-4 rounded-xl border border-border/60 bg-muted/20 p-4">
            <h3 class="text-sm font-semibold">Models</h3>
            <div v-for="name in ['ocr', 'extract', 'translate']" :key="name" class="space-y-2">
              <label class="text-sm font-medium capitalize" :for="`${name}-model`">{{ name }} model</label>
              <select :id="`${name}-model`" v-model="selectedModels[name as 'ocr' | 'extract' | 'translate']" class="h-10 w-full rounded-md border border-input bg-background px-3 text-sm" :aria-label="`${name} model`">
                <option v-for="option in modelOptions(name as 'ocr' | 'extract' | 'translate')" :key="option" :value="option">{{ option }}</option>
              </select>
            </div>
          </div>
        </div>
        <div class="mt-5 flex items-center justify-end">
          <button data-testid="upload-submit" class="inline-flex h-10 items-center justify-center rounded-md bg-primary px-5 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50" type="button" :aria-describedby="hasUploadValidationError ? 'upload-validation-error' : undefined" :title="uploadInvalid ? 'Resolve upload validation errors before submitting.' : undefined" :disabled="uploadInvalid || uploadLoading" @click="upload">
            {{ uploadLoading ? 'Uploading…' : 'Upload and process' }}
          </button>
        </div>
      </section>

      <section class="space-y-3" aria-labelledby="recent-batches-title">
        <div class="flex items-center justify-between gap-3">
          <h2 id="recent-batches-title" class="text-xl font-semibold">Recent batches</h2>
          <button class="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-50" type="button" :disabled="loadingBatches" @click="refreshBatches">Refresh</button>
        </div>
        <div v-if="loadingBatches" class="rounded-xl border border-border/60 p-6 text-sm text-muted-foreground" role="status">Loading batches…</div>
        <div v-else-if="batches.length === 0" class="rounded-xl border border-dashed border-border p-8 text-center text-sm text-muted-foreground">No batches yet.</div>
        <div v-else class="grid gap-3 md:grid-cols-2">
          <button v-for="batch in batches" :key="batch.id" class="rounded-xl border border-border/60 bg-card p-4 text-left shadow-sm transition hover:border-primary/50 hover:shadow-card" type="button" @click="openBatch(batch.id)">
            <div class="flex items-center justify-between gap-3">
              <span class="font-mono text-xs text-muted-foreground">{{ batch.id }}</span>
              <span class="text-xs text-muted-foreground">{{ batch.job_count }} jobs</span>
            </div>
            <div class="mt-3 flex flex-wrap gap-2 text-xs">
              <span v-for="(count, status) in batch.status_counts" :key="status" class="rounded-full bg-muted px-2 py-1">{{ status }}: {{ count }}</span>
            </div>
          </button>
        </div>
      </section>
    </template>
  </div>
</template>
