<script setup lang="ts">
import { computed } from 'vue'
import type { SearchResponse } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import RenderedMarkdown from '@/components/RenderedMarkdown.vue'
import FavoriteRatingControl from '@/components/favorites/FavoriteRatingControl.vue'
import { resolveStaticBaseUrl } from '@/lib/static-base'
import { normalizeSummaryText, summaryParagraphs } from '@/lib/summary-text'
import { useRuntimeConfigStore } from '@/stores/runtime-config'
import { useI18n } from 'vue-i18n'
import type { FavoriteRating } from '@/types/favorites'

const props = withDefaults(defineProps<{
  item: SearchResponse['items'][number]
  displayIndex: number
  isSelected: boolean
  selectionFull: boolean
  isFavorite?: boolean
  favoriteRating?: FavoriteRating
  expanded?: boolean
  expandedMarkdown?: string
  expandedLoading?: boolean
  snippetRenderer: (value: string) => string
}>(), {
  expanded: false,
  expandedMarkdown: '',
  expandedLoading: false,
  isFavorite: false,
})

const emit = defineEmits<{
  toggleSelect: []
  toggleSummary: []
  toggleFavorite: []
  setFavoriteRating: [rating: FavoriteRating]
}>()

const { t } = useI18n()
const translatedUrl = computed(() => Object.values(props.item.translated_md_urls || {})[0] || '')
const normalizedSnippetMarkdown = computed(() => normalizeSummaryText(props.item.snippet_markdown))
const previewParagraphs = computed(() => summaryParagraphs(props.item.summary_preview))
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
  <div class="group rounded-xl border border-border/60 bg-card p-5 shadow-elevated transition-all duration-200 hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-elevated-lg">
    <div class="flex flex-col gap-3">
      <div class="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div class="space-y-1">
          <router-link
            :to="{ name: 'paper', params: { paperId: item.paper_id } }"
            class="font-display-serif text-lg font-semibold leading-snug tracking-tight text-foreground transition-colors hover:text-primary"
          >
            {{ item.title }}
          </router-link>
          <div class="text-xs text-muted-foreground">{{ item.venue }} · {{ item.year }}</div>
          <div v-if="item.authors?.length" class="text-xs text-muted-foreground/80">
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
          <FavoriteRatingControl
            :is-favorite="isFavorite"
            :rating="favoriteRating"
            @toggle-favorite="emit('toggleFavorite')"
            @set-rating="emit('setFavoriteRating', $event)"
          />
          <Badge variant="slate">#{{ displayIndex }}</Badge>
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
            class="prose prose-sm max-w-none text-foreground/80 dark:prose-invert"
          />
          <div v-else-if="normalizedSnippetMarkdown" class="prose prose-sm max-w-none text-foreground/80 dark:prose-invert">
            <div v-html="snippetRenderer(normalizedSnippetMarkdown)"></div>
          </div>
          <div v-else-if="previewParagraphs.length" class="text-sm leading-relaxed text-foreground/80 summary-clamp">
            <p v-for="(paragraph, index) in previewParagraphs" :key="index" class="whitespace-pre-line">
              {{ paragraph }}
            </p>
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

      <div class="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <span class="font-semibold text-foreground/70">{{ t('resources') }}</span>
        <Badge v-if="item.has_pdf" variant="pdf">PDF</Badge>
        <Badge v-if="item.has_source" variant="teal">Source</Badge>
        <Badge v-if="item.has_translated" variant="violet">Translated</Badge>
        <Badge v-if="item.preferred_summary_template" variant="navy">Summary</Badge>
      </div>
    </div>
  </div>
</template>
