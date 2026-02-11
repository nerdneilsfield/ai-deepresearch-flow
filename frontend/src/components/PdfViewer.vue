<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { VuePDFjs } from '@tuttarealstep/vue-pdf.js'
import '@tuttarealstep/vue-pdf.js/dist/style.css'
import enUS_FTL from '@tuttarealstep/vue-pdf.js/l10n/en-US/viewer.ftl?raw'

const props = defineProps<{
  url?: string | null
  fit?: boolean
  fullHeight?: boolean
}>()

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

watch(
  () => props.url,
  (url) => {
    if (url) {
      console.log('[PdfViewer] Loading PDF:', url)
    } else {
      console.log('[PdfViewer] No PDF URL')
    }
  },
  { immediate: true }
)

</script>

<template>
  <div v-if="!url" class="text-sm text-ink-500 p-4">No PDF available.</div>
  <div
    v-else
    class="pdf-container relative w-full rounded-lg border border-ink-100 bg-white overflow-hidden"
    :style="{ height: containerHeight, minHeight: '500px' }"
  >
    <VuePDFjs
      :key="url"
      :source="url"
      :options="options"
      class="w-full h-full"
    />
  </div>
</template>
