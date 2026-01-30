<script setup lang="ts">
import { ref, watch } from 'vue'
import { fetchText } from '@/lib/api'
import MarkdownContent from './MarkdownContent.vue'

const markdownCache = new Map<string, string>()

const props = defineProps<{
  url?: string | null
  imagesBaseUrl?: string | null
  placeholder?: string
}>()

const markdown = ref('')
const loading = ref(false)
const error = ref('')

async function load(url: string) {
  if (markdownCache.has(url)) {
    markdown.value = markdownCache.get(url) || ''
    return
  }
  loading.value = true
  error.value = ''
  try {
    const raw = await fetchText(url)
    markdownCache.set(url, raw)
    markdown.value = raw
  } catch (err) {
    error.value = 'Failed to load markdown.'
  } finally {
    loading.value = false
  }
}

watch(
  () => props.url,
  (next) => {
    if (!next) {
      markdown.value = ''
      error.value = ''
      loading.value = false
      return
    }
    void load(next)
  },
  { immediate: true }
)
</script>

<template>
  <div v-if="loading" class="mt-4 text-sm text-ink-500">Loading markdown...</div>
  <div v-else-if="error" class="mt-4 text-sm text-red-600">{{ error }}</div>
  <MarkdownContent
    v-else-if="markdown"
    :markdown="markdown"
    :images-base-url="props.imagesBaseUrl || ''"
    class="prose prose-sm mt-4 max-w-none"
  />
  <div v-else class="mt-4 text-sm text-ink-500">{{ props.placeholder || 'No markdown available.' }}</div>
</template>
