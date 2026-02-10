<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import MarkdownContent from './MarkdownContent.vue'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{
  summaryUrls: Record<string, string>
  summaryAvailable: string[]
  summaryMarkdown: string
  summaryMeta?: Record<string, any> | null
  imagesBaseUrl?: string | null
}>()

const model = defineModel<string>()
const ui = useUiStore()
const { t } = useI18n()

const available = computed(() => {
  if (props.summaryAvailable.length) return props.summaryAvailable
  return Object.keys(props.summaryUrls)
})

function setTemplate(tag: string) {
  if (!props.summaryUrls[tag]) {
    ui.pushToast('Template not available in this snapshot', 'error')
    return
  }
  model.value = tag
}
</script>

<template>
  <Tabs v-model="model" class="w-full">
    <TabsList class="flex flex-wrap gap-2">
      <TabsTrigger v-for="(_, tag) in props.summaryUrls" :key="tag" :value="String(tag)">
        {{ tag }}
      </TabsTrigger>
    </TabsList>
    <TabsContent v-if="model" :value="String(model)">
      <div class="mt-3 flex flex-wrap gap-2 text-xs text-ink-500 dark:text-ink-400">
        <span class="font-semibold text-ink-700 dark:text-ink-300">{{ t('available') }}</span>
        <button
          v-for="tag in available"
          :key="tag"
          type="button"
          class="rounded-full border border-ink-200 px-2 py-0.5 text-xs dark:border-ink-700"
          :class="props.summaryUrls[tag] ? 'text-ink-700 hover:border-ink-400 dark:text-ink-300 dark:hover:border-ink-500' : 'opacity-40'"
          @click="setTemplate(tag)"
        >
          {{ tag }}
        </button>
      </div>
      <div v-if="props.summaryMeta && props.summaryMeta.title" class="mt-4 space-y-3 text-sm text-ink-700 dark:text-ink-300">
        <h2 class="text-xl font-semibold text-ink-900 dark:text-ink-100">{{ props.summaryMeta.title }}</h2>
        <div v-if="props.summaryMeta.authors?.length">
          <span class="font-semibold">{{ t('authorsLabel') }}:</span>
          <span class="ml-2">{{ props.summaryMeta.authors.join(', ') }}</span>
        </div>
        <div v-if="props.summaryMeta.institutions?.length">
          <span class="font-semibold">{{ t('institutionsLabel') }}:</span>
          <span class="ml-2">{{ props.summaryMeta.institutions.join(', ') }}</span>
        </div>
        <div class="flex flex-wrap gap-4 text-sm text-ink-600 dark:text-ink-400">
          <div v-if="props.summaryMeta.outputLanguage">
            <span class="font-semibold">{{ t('outputLanguageLabel') }}:</span>
            <span class="ml-2">{{ props.summaryMeta.outputLanguage }}</span>
          </div>
          <div v-if="props.summaryMeta.publicationDate">
            <span class="font-semibold">{{ t('publicationDateLabel') }}:</span>
            <span class="ml-2">{{ props.summaryMeta.publicationDate }}</span>
          </div>
          <div v-if="props.summaryMeta.venue">
            <span class="font-semibold">{{ t('venueLabel') }}:</span>
            <span class="ml-2">{{ props.summaryMeta.venue }}</span>
          </div>
        </div>
        <div v-if="props.summaryMeta.keywords?.length">
          <span class="font-semibold">{{ t('keywordsLabel') }}:</span>
          <span class="ml-2">{{ props.summaryMeta.keywords.join(', ') }}</span>
        </div>
        <div v-if="props.summaryMeta.abstract" class="mt-4">
          <div class="text-base font-semibold text-ink-900 dark:text-ink-100">{{ t('abstractLabel') }}</div>
          <div class="mt-2 text-ink-700 dark:text-ink-300">{{ props.summaryMeta.abstract }}</div>
        </div>
      </div>
      <MarkdownContent
        v-if="props.summaryMarkdown"
        :markdown="props.summaryMarkdown"
        :images-base-url="props.imagesBaseUrl || ''"
        class="prose prose-sm mt-4 max-w-none"
      />
      <div v-else class="mt-4 text-sm text-ink-500">{{ t('noData') }}</div>
    </TabsContent>
  </Tabs>
</template>
