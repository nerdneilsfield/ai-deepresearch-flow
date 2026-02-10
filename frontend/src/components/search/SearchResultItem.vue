<script setup lang="ts">
import { computed } from 'vue'
import type { SearchResponse } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import RenderedMarkdown from '@/components/RenderedMarkdown.vue'
import { resolveStaticBaseUrl } from '@/lib/static-base'
import { useRuntimeConfigStore } from '@/stores/runtime-config'
import { useI18n } from 'vue-i18n'

const props = withDefaults(defineProps<{
  item: SearchResponse['items'][number]
  displayIndex: number
  isSelected: boolean
  selectionFull: boolean
  expanded?: boolean
  expandedMarkdown?: string
  expandedLoading?: boolean
  snippetRenderer: (value: string) => string
}>(), {
  expanded: false,
  expandedMarkdown: '',
  expandedLoading: false,
})

const emit = defineEmits<{
  toggleSelect: []
  toggleSummary: []
}>()

const { t } = useI18n()
const translatedUrl = computed(() => Object.values(props.item.translated_md_urls || {})[0] || '')
const runtimeConfig = useRuntimeConfigStore()
const imagesBaseUrl = computed(() =>
  resolveStaticBaseUrl(
    runtimeConfig.staticBaseUrl,
    props.item.images_base_url,
    props.item.manifest_url,
    props.item.summary_url,
    props.item.source_md_url,
    translatedUrl.value,
    props.item.pdf_url
  )
)

function formatAuthors(authors?: string[]) {
  if (!authors || !authors.length) return ''
  const visible = authors.slice(0, 4)
  const remaining = authors.length - visible.length
  return remaining > 0 ? `${visible.join(', ')} +${remaining}` : visible.join(', ')
}
</script>

<template>
  <div class="rounded-xl border border-ink-100 bg-white p-4 shadow-card transition-all hover:shadow-card-hover hover:border-ink-200 dark:border-ink-700 dark:bg-ink-800 dark:hover:border-ink-600">
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div class="space-y-1">
          <router-link
            :to="`/paper/${item.paper_id}`"
            class="text-lg font-semibold text-ink-900 hover:text-accent-600 dark:text-ink-100 dark:hover:text-accent-400"
          >
            {{ item.title }}
          </router-link>
          <div class="text-xs text-ink-500 dark:text-ink-400">{{ item.venue }} · {{ item.year }}</div>
          <div v-if="item.authors?.length" class="text-xs text-ink-400 dark:text-ink-500">
            {{ formatAuthors(item.authors) }}
          </div>
        </div>
        <div class="flex flex-row gap-2 sm:flex-col sm:items-end">
          <TooltipProvider>
            <Tooltip v-if="selectionFull && !isSelected">
              <TooltipTrigger as-child>
                <Button size="sm" variant="outline" @click="emit('toggleSelect')">
                  {{ t('select') }}
                </Button>
              </TooltipTrigger>
              <TooltipContent>{{ t('selectionLimitReached') }}</TooltipContent>
            </Tooltip>
            <Button v-else size="sm" variant="outline" @click="emit('toggleSelect')">
              {{ isSelected ? t('selected_btn') : t('select') }}
            </Button>
          </TooltipProvider>
          <Badge variant="outline">#{{ displayIndex }}</Badge>
        </div>
      </div>

      <div class="flex items-start gap-3">
        <div class="flex-1">
          <RenderedMarkdown
            v-if="expanded && expandedMarkdown"
            :markdown="expandedMarkdown"
            :images-base-url="imagesBaseUrl"
            :enable-outline="false"
            :enable-markmap="false"
            :enable-images="false"
            class="prose prose-sm max-w-none text-ink-700 dark:text-ink-300 dark:prose-invert"
          />
          <div v-else-if="item.snippet_markdown" class="prose prose-sm max-w-none text-ink-700 dark:text-ink-300 dark:prose-invert">
            <div v-html="snippetRenderer(item.snippet_markdown)"></div>
          </div>
          <div v-else-if="item.summary_preview" class="text-sm text-ink-700 dark:text-ink-300 summary-clamp">
            {{ item.summary_preview }}
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          class="shrink-0"
          @click="emit('toggleSummary')"
          :aria-label="expanded ? t('collapseSummary') : t('expandSummary')"
        >
          <span v-if="expandedLoading">{{ t('loading') }}</span>
          <ChevronUp v-else-if="expanded" />
          <ChevronDown v-else />
        </Button>
      </div>

      <div class="flex flex-wrap items-center gap-2 text-[11px] text-ink-500 dark:text-ink-400">
        <span class="font-semibold text-ink-700 dark:text-ink-300">{{ t('resources') }}</span>
        <Badge v-if="item.has_pdf" variant="outline">PDF</Badge>
        <Badge v-if="item.has_source" variant="outline">Source</Badge>
        <Badge v-if="item.has_translated" variant="outline">Translated</Badge>
        <Badge v-if="item.preferred_summary_template" variant="outline">Summary</Badge>
      </div>
    </div>
  </div>
</template>
