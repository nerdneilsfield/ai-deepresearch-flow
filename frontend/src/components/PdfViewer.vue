<script setup lang="ts">
import { computed, onErrorCaptured, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { VuePDFjs } from '@tuttarealstep/vue-pdf.js'
import '@tuttarealstep/vue-pdf.js/dist/style.css'
import enUS_FTL from '@tuttarealstep/vue-pdf.js/l10n/en-US/viewer.ftl?raw'

const props = defineProps<{
  url?: string | null
  fit?: boolean
  fullHeight?: boolean
}>()

const { t } = useI18n()
const viewerError = ref<unknown>(null)
// Download progress; the overlay stays until the first page count is known so users never see a bare "0 of 0".
const loadedBytes = ref(0)
const totalBytes = ref(0)
const ready = ref(false)
const attempt = ref(0)
const viewer = ref<{ pdfPages?: number } | null>(null)
const mb = (n: number) => (n / 1024 / 1024).toFixed(1)
const containerHeight = computed(() => {
  if (props.fullHeight) return 'calc(100vh - 220px)'
  if (props.fit) return '100%'
  return '70vh'
})

const options = reactive({
  locale: {
    code: 'en-US',
    ftl: enUS_FTL,
  },
})

function setViewerError(error: unknown) {
  viewerError.value = error
}

const sourceOptions = {
  onError: setViewerError,
  onProgress: ({ loaded, total }: { loaded: number; total: number }) => {
    loadedBytes.value = loaded
    totalBytes.value = total
  },
}

watch(() => viewer.value?.pdfPages, (pages) => {
  if (pages) ready.value = true
})

function retry() {
  viewerError.value = null
  ready.value = false
  loadedBytes.value = 0
  totalBytes.value = 0
  attempt.value += 1
}

watch(
  () => props.url,
  (url) => {
    viewerError.value = null
    ready.value = false
    loadedBytes.value = 0
    totalBytes.value = 0
    if (url) {
      console.log('[PdfViewer] Loading PDF:', url)
    } else {
      console.log('[PdfViewer] No PDF URL')
    }
  },
  { immediate: true }
)

onErrorCaptured((error) => {
  setViewerError(error)
  return false
})

const viewerErrorMessage = computed(() => {
  if (viewerError.value instanceof Error && viewerError.value.message) return viewerError.value.message
  if (typeof viewerError.value === 'string' && viewerError.value) return viewerError.value
  return t('pdfRetryHint')
})
</script>

<template>
  <div v-if="!url" class="text-sm text-ink-500 p-4">{{ t('pdfUnavailable') }}</div>
  <div
    v-else-if="viewerError"
    role="alert"
    class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"
  >
    <p class="font-medium">{{ t('pdfLoadFailed') }}</p>
    <p class="mt-2 break-words opacity-80">{{ viewerErrorMessage }}</p>
    <div class="mt-3 flex gap-4">
      <button type="button" class="text-blue-700 underline dark:text-blue-300" @click="retry">{{ t('retry') }}</button>
      <a :href="url" target="_blank" rel="noopener noreferrer" class="text-blue-700 underline dark:text-blue-300">
        {{ t('pdfOpenDirect') }}
      </a>
    </div>
  </div>
  <div
    v-else
    class="pdf-container relative w-full rounded-lg border border-ink-100 bg-white overflow-hidden"
    :style="{ height: containerHeight, minHeight: '500px' }"
  >
    <div
      v-if="!ready"
      class="absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-white/90 text-sm text-ink-600"
      role="status"
    >
      <svg class="h-5 w-5 animate-spin" viewBox="0 0 24 24" fill="none">
        <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
        <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
      </svg>
      <span v-if="totalBytes">{{ t('pdfDownloading', { loaded: mb(loadedBytes), total: mb(totalBytes) }) }}</span>
      <span v-else-if="loadedBytes">{{ t('pdfDownloadingUnknown', { loaded: mb(loadedBytes) }) }}</span>
      <span v-else>{{ t('pdfConnecting') }}</span>
    </div>
    <VuePDFjs
      ref="viewer"
      :key="`${url}#${attempt}`"
      :source="url"
      :options="options"
      :source-options="sourceOptions"
      class="w-full h-full"
    />
  </div>
</template>
