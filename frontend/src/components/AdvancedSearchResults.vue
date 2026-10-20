<script setup lang="ts">
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import type { AdvancedSearchResult } from '@/lib/advanced-search'
import SearchEmptyState from '@/components/search/SearchEmptyState.vue'
import ResultActions from '@/components/search/ResultActions.vue'
import { splitHighlight } from '@/lib/search-expression'
import type { FavoriteRating } from '@/types/favorites'

const props = defineProps<{
  results: AdvancedSearchResult[]
  degraded?: boolean
  degradationReason?: string | null
  degradationMessage?: string | null
  selectedIds?: Set<string>
  selectionFull?: boolean
  favoriteIds?: Set<string>
  favoriteRatings?: Record<string, FavoriteRating>
  /** Free-text terms of the query, highlighted inside the matched chunk. */
  highlightTerms?: string[]
  /** Labels of conditions the user could drop, shown in the empty state. */
  removableHints?: string[]
}>()

const emit = defineEmits<{
  toggleSelect: [result: AdvancedSearchResult]
  toggleFavorite: [result: AdvancedSearchResult]
  setFavoriteRating: [result: AdvancedSearchResult, rating: FavoriteRating]
}>()

const router = useRouter()
const { t } = useI18n()

function scoreTitle(result: AdvancedSearchResult) {
  const { dense, sparse, fused, reranker, final } = result.scores
  return [
    dense !== undefined && `dense ${dense.toFixed(4)}`,
    sparse !== undefined && `sparse ${sparse.toFixed(2)}`,
    `fused ${fused.toFixed(4)}`,
    reranker !== undefined && `rerank ${reranker.toFixed(3)}`,
    `final ${final.toFixed(3)}`,
  ].filter(Boolean).join(' · ')
}

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

function onToggleSelect(result: AdvancedSearchResult) {
  emit('toggleSelect', result)
}

function onToggleFavorite(result: AdvancedSearchResult) {
  emit('toggleFavorite', result)
}

function onSetFavoriteRating(result: AdvancedSearchResult, rating: FavoriteRating) {
  emit('setFavoriteRating', result, rating)
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

    <SearchEmptyState
      v-if="results.length === 0"
      :hints="removableHints"
      data-testid="advanced-results-empty"
    />

    <article
      v-for="result in results"
      :key="result.chunk_id"
      class="cursor-pointer rounded-xl border border-border/60 bg-card p-4 text-card-foreground shadow-elevated transition-all hover:border-primary/30 hover:shadow-elevated-lg dark:border-ink-700 dark:bg-ink-900/80 dark:hover:border-ink-600 sm:p-5"
      data-testid="advanced-result-card"
      role="link"
      tabindex="0"
      @click="openPaper(result)"
      @keydown.enter.prevent="openPaper(result)"
      @keydown.space.prevent="openPaper(result)"
    >
      <h3 class="font-display-serif text-lg font-semibold leading-snug tracking-tight text-foreground dark:text-ink-100">{{ result.paper.title }}</h3>
      <p class="mt-1 text-xs text-muted-foreground dark:text-ink-400">
        <span v-if="result.paper.authors.length">{{ result.paper.authors.join(', ') }} — </span>{{ [result.paper.venue, result.paper.year].filter(Boolean).join(' · ') }}
      </p>
      <p class="mt-2.5 text-sm leading-relaxed text-foreground/80 dark:text-ink-200">
        <template v-for="(part, index) in splitHighlight(result.chunk.text, highlightTerms ?? [])" :key="index">
          <mark
            v-if="part.hit"
            class="rounded bg-amber-300/80 px-0.5 font-semibold text-amber-950 dark:bg-amber-400/30 dark:text-amber-100"
          >{{ part.text }}</mark>
          <template v-else>{{ part.text }}</template>
        </template>
      </p>
      <div class="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-border/50 pt-2 dark:border-ink-700">
        <div class="flex min-w-0 items-center gap-2 text-[11px] text-muted-foreground dark:text-ink-400">
          <span class="truncate rounded bg-muted px-1.5 py-0.5 font-mono dark:bg-ink-800" :title="t('chunkSource', { source: result.chunk.field_name })">{{ result.chunk.field_name }}</span>
          <span class="shrink-0 tabular-nums text-muted-foreground/60" :title="scoreTitle(result)" data-testid="advanced-result-score">
            {{ t('scoreLabel', { score: result.scores.final.toFixed(3) }) }}
          </span>
        </div>
        <ResultActions
          select-test-id="advanced-result-select"
          :is-selected="selectedIds?.has(result.paper_id) ?? false"
          :selection-full="selectionFull ?? false"
          :is-favorite="favoriteIds?.has(result.paper_id) ?? false"
          :favorite-rating="favoriteRatings?.[result.paper_id]"
          @toggle-select="onToggleSelect(result)"
          @toggle-favorite="onToggleFavorite(result)"
          @set-favorite-rating="onSetFavoriteRating(result, $event)"
        />
      </div>
    </article>
  </section>
</template>
