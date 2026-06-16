<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { AdvancedSearchResult } from '@/lib/advanced-search'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

const props = defineProps<{
  results: AdvancedSearchResult[]
  degraded?: boolean
  degradationReason?: string | null
  degradationMessage?: string | null
  selectedIds?: Set<string>
  selectionFull?: boolean
}>()

const emit = defineEmits<{
  toggleSelect: [result: AdvancedSearchResult]
}>()

const router = useRouter()
const { t } = useI18n()

function openPaper(result: AdvancedSearchResult) {
  router.push({
    name: 'paper',
    params: { paperId: result.paper_id },
    query: {
      advanced_chunk_id: result.chunk_id,
      advanced_chunk_text: result.chunk.text,
      advanced_chunk_field: result.chunk.field_name,
    },
  })
}

function onToggleSelect(event: Event, result: AdvancedSearchResult) {
  event.stopPropagation()
  emit('toggleSelect', result)
}
</script>

<template>
  <section class="space-y-3">
    <div
      v-if="degraded"
      class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-200"
      data-testid="advanced-degraded-banner"
    >
      Results are degraded: {{ degradationMessage ?? degradationReason ?? 'unknown' }}
    </div>

    <div
      v-if="results.length === 0"
      class="rounded-lg border border-border/60 bg-card px-4 py-6 text-sm text-muted-foreground dark:border-ink-700 dark:bg-ink-900/80 dark:text-ink-300"
      data-testid="advanced-results-empty"
    >
      No results.
    </div>

    <article
      v-for="result in results"
      :key="result.chunk_id"
      class="cursor-pointer rounded-xl border border-border/60 bg-card p-4 text-card-foreground transition-all hover:border-border hover:shadow-card-hover dark:border-ink-700 dark:bg-ink-900/80 dark:hover:border-ink-600"
      data-testid="advanced-result-card"
      role="link"
      tabindex="0"
      @click="openPaper(result)"
      @keydown.enter.prevent="openPaper(result)"
      @keydown.space.prevent="openPaper(result)"
    >
      <h3 class="text-base font-semibold text-foreground dark:text-ink-100">{{ result.paper.title }}</h3>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-400">
        {{ result.paper.authors.join(', ') }} · {{ result.paper.year }} · {{ result.paper.venue }}
      </p>
      <div class="mt-3 flex items-center justify-between gap-2">
        <div class="text-xs text-muted-foreground dark:text-ink-400">{{ result.chunk.field_name }}</div>
        <TooltipProvider>
          <Tooltip v-if="selectionFull && !selectedIds?.has(result.paper_id)">
            <TooltipTrigger as-child>
              <Button
                size="sm"
                variant="outline"
                data-testid="advanced-result-select"
                @click="onToggleSelect($event, result)"
              >
                {{ t('select') }}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{{ t('selectionLimitReached') }}</TooltipContent>
          </Tooltip>
          <Button
            v-else
            size="sm"
            variant="outline"
            data-testid="advanced-result-select"
            @click="onToggleSelect($event, result)"
          >
            {{ selectedIds?.has(result.paper_id) ? t('selected_btn') : t('select') }}
          </Button>
        </TooltipProvider>
      </div>
      <p class="mt-3 text-sm text-foreground/80 dark:text-ink-200">{{ result.chunk.text }}</p>
      <dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground dark:text-ink-400 sm:grid-cols-5">
        <template v-if="result.scores.dense !== undefined">
          <dt>dense</dt>
          <dd>{{ result.scores.dense.toFixed(4) }}</dd>
        </template>
        <template v-if="result.scores.sparse !== undefined">
          <dt>sparse</dt>
          <dd>{{ result.scores.sparse.toFixed(2) }}</dd>
        </template>
        <dt>fused</dt>
        <dd>{{ result.scores.fused.toFixed(4) }}</dd>
        <template v-if="result.scores.reranker !== undefined">
          <dt>rerank</dt>
          <dd>{{ result.scores.reranker.toFixed(3) }}</dd>
        </template>
        <dt>final</dt>
        <dd>{{ result.scores.final.toFixed(3) }}</dd>
      </dl>
    </article>
  </section>
</template>
