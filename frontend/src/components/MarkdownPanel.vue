<script setup lang="ts">
import { ref, watch } from 'vue'
import { fetchText, getTranslatedMarkdownCached } from '@/lib/api'
import { cacheSet, markdownCache } from '@/lib/markdown-cache'
import MarkdownContent from './MarkdownContent.vue'

const props = defineProps<{
  url?: string | null
  imagesBaseUrl?: string | null
  placeholder?: string
  cachePaperId?: string | null
  cacheTranslationLang?: string | null
}>()

const markdown = ref('')
const loading = ref(false)
const error = ref('')
let loadGeneration = 0

async function load(url: string) {
  console.log('[MarkdownPanel] Loading URL:', url)
  if (markdownCache.has(url)) {
    markdown.value = markdownCache.get(url) || ''
    console.log('[MarkdownPanel] ✓ Loaded from cache, length:', markdown.value.length)
    return
  }
  const gen = ++loadGeneration
  loading.value = true
  error.value = ''
  console.log('[MarkdownPanel] Fetching from server...')
  try {
    const raw = props.cachePaperId && props.cacheTranslationLang
      ? await getTranslatedMarkdownCached(props.cachePaperId, props.cacheTranslationLang, url)
      : await fetchText(url)
    // Guard against stale responses when URL changed during fetch
    if (gen !== loadGeneration) {
      console.log('[MarkdownPanel] ✗ Stale response, ignoring')
      return
    }
    cacheSet(url, raw)
    markdown.value = raw
    console.log('[MarkdownPanel] ✓ Success, length:', raw.length)
  } catch (err) {
    if (gen !== loadGeneration) return
    console.error('[MarkdownPanel] ✗ Error:', err)
    error.value = 'Failed to load markdown.'
  } finally {
    if (gen === loadGeneration) loading.value = false
  }
}

watch(
  () => props.url,
  (next) => {
    if (!next) {
      loadGeneration++
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
