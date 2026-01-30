<script setup lang="ts">
import { computed } from 'vue'
import type { SearchResponse } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import RenderedMarkdown from '@/components/RenderedMarkdown.vue'
import { resolveStaticBaseUrl } from '@/lib/static-base'

const props = withDefaults(defineProps<{
  item: SearchResponse['items'][number]
  displayIndex: number
  isSelected: boolean
  selectionFull: boolean
  expanded?: boolean
  expandedMarkdown?: string
  expandedLoading?: boolean
  snippetRenderer: (value: string) => string
  onToggleSelect: () => void
  onToggleSummary: () => void
}>(), {
  expanded: false,
  expandedMarkdown: '',
  expandedLoading: false,
})

const translatedUrl = computed(() => Object.values(props.item.translated_md_urls || {})[0] || '')
const imagesBaseUrl = computed(() =>
  resolveStaticBaseUrl(
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
  <div class="rounded-xl border border-ink-100 bg-white p-4">
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div class="space-y-1">
          <router-link
            :to="`/paper/${item.paper_id}`"
            class="text-lg font-semibold text-ink-900 hover:text-accent-600"
          >
            {{ item.title }}
          </router-link>
          <div class="text-xs text-ink-500">{{ item.venue }} · {{ item.year }}</div>
          <div v-if="item.authors?.length" class="text-xs text-ink-400">
            {{ formatAuthors(item.authors) }}
          </div>
        </div>
        <div class="flex flex-row gap-2 sm:flex-col sm:items-end">
          <TooltipProvider>
            <Tooltip v-if="selectionFull && !isSelected">
              <TooltipTrigger as-child>
                <Button size="sm" variant="outline" @click="onToggleSelect">
                  Select
                </Button>
              </TooltipTrigger>
              <TooltipContent>Selection limit reached</TooltipContent>
            </Tooltip>
            <Button v-else size="sm" variant="outline" @click="onToggleSelect">
              {{ isSelected ? 'Selected' : 'Select' }}
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
            class="prose prose-sm max-w-none text-ink-700"
          />
          <div v-else-if="item.snippet_markdown" class="prose prose-sm max-w-none text-ink-700">
            <div v-html="snippetRenderer(item.snippet_markdown)"></div>
          </div>
          <div v-else-if="item.summary_preview" class="text-sm text-ink-700 summary-clamp">
            {{ item.summary_preview }}
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          class="shrink-0"
          @click="onToggleSummary"
          :aria-label="expanded ? 'Collapse summary' : 'Expand summary'"
        >
          <span v-if="expandedLoading">Loading</span>
          <ChevronUp v-else-if="expanded" />
          <ChevronDown v-else />
        </Button>
      </div>

      <div class="flex flex-wrap items-center gap-2 text-[11px] text-ink-500">
        <span class="font-semibold text-ink-700">Resources</span>
        <Badge v-if="item.has_pdf" variant="outline">PDF</Badge>
        <Badge v-if="item.has_source" variant="outline">Source</Badge>
        <Badge v-if="item.has_translated" variant="outline">Translated</Badge>
        <Badge v-if="item.preferred_summary_template" variant="outline">Summary</Badge>
      </div>
    </div>
  </div>
</template>
