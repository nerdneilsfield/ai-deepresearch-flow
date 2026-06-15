<script setup lang="ts">
import { computed, onErrorCaptured, reactive, ref, watch } from 'vue'
import { VuePDFjs } from '@tuttarealstep/vue-pdf.js'
import '@tuttarealstep/vue-pdf.js/dist/style.css'
import enUS_FTL from '@tuttarealstep/vue-pdf.js/l10n/en-US/viewer.ftl?raw'

const props = defineProps<{
  url?: string | null
  fit?: boolean
  fullHeight?: boolean
}>()

const viewerError = ref<unknown>(null)
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
}

watch(
  () => props.url,
  (url) => {
    viewerError.value = null
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
  return 'Please reload this page or download the PDF directly.'
})
</script>

<template>
  <div v-if="!url" class="text-sm text-ink-500 p-4">No PDF available.</div>
  <div
    v-else-if="viewerError"
    role="alert"
    class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-950 dark:border-red-900 dark:bg-red-950/30 dark:text-red-100"
  >
    <p class="font-medium">PDF viewer is unavailable.</p>
    <p class="mt-2 break-words opacity-80">{{ viewerErrorMessage }}</p>
    <a :href="url" target="_blank" rel="noopener noreferrer" class="mt-3 inline-flex text-blue-700 underline dark:text-blue-300">
      Open PDF directly
    </a>
  </div>
  <div
    v-else
    class="pdf-container relative w-full rounded-lg border border-ink-100 bg-white overflow-hidden"
    :style="{ height: containerHeight, minHeight: '500px' }"
  >
    <VuePDFjs
      :key="url"
      :source="url"
      :options="options"
      :source-options="sourceOptions"
      class="w-full h-full"
    />
  </div>
</template>
