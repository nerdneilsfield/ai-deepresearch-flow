<script setup lang="ts">
import { useRouter } from 'vue-router'
import type { AdvancedSearchResult } from '@/lib/advanced-search'

const props = defineProps<{
  results: AdvancedSearchResult[]
  degraded?: boolean
  degradationReason?: string | null
  degradationMessage?: string | null
}>()

const router = useRouter()

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
</script>

<template>
  <section class="space-y-3">
    <div
      v-if="degraded"
      class="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800"
      data-testid="advanced-degraded-banner"
    >
      Results are degraded: {{ degradationMessage ?? degradationReason ?? 'unknown' }}
    </div>

    <div
      v-if="results.length === 0"
      class="rounded-lg border border-ink-100 bg-white px-4 py-6 text-sm text-ink-500"
      data-testid="advanced-results-empty"
    >
      No results.
    </div>

    <article
      v-for="result in results"
      :key="result.chunk_id"
      class="cursor-pointer rounded-xl border border-ink-100 bg-white p-4 transition-all hover:border-ink-200 hover:shadow-card-hover"
      data-testid="advanced-result-card"
      role="link"
      tabindex="0"
      @click="openPaper(result)"
      @keydown.enter.prevent="openPaper(result)"
      @keydown.space.prevent="openPaper(result)"
    >
      <h3 class="text-base font-semibold text-ink-900">{{ result.paper.title }}</h3>
      <p class="mt-1 text-sm text-ink-500">
        {{ result.paper.authors.join(', ') }} · {{ result.paper.year }} · {{ result.paper.venue }}
      </p>
      <p class="mt-3 text-sm text-ink-700">{{ result.chunk.text }}</p>
      <dl class="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-ink-500 sm:grid-cols-5">
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
