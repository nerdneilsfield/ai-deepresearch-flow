<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { DEFAULT_PAGE_SIZE } from '@/lib/config'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useFacetStats } from '@/composables/useFacetStats'
import { useSelectionStore } from '@/stores/selection'
import { fetchJson } from '@/lib/api'
import { lazySnippet } from '@/lib/lazy'
import SearchResultItem from '@/components/search/SearchResultItem.vue'


const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const selection = useSelectionStore()

const facet = computed(() => String(route.params.facet || ''))
const rawValue = computed(() => String(route.params.value || ''))
const facetValue = computed(() => {
  try {
    return decodeURIComponent(rawValue.value)
  } catch {
    return rawValue.value
  }
})

const page = ref(1)
const pageSize = ref(DEFAULT_PAGE_SIZE)
const { statsQuery, papersQuery } = useFacetStats(facet, facetValue, page, pageSize)
const stats = computed(() => statsQuery.data.value ?? null)
const papers = computed(() => papersQuery.data.value ?? null)
const loadingStats = computed(() => statsQuery.isFetching.value)
const loadingPapers = computed(() => papersQuery.isFetching.value)
const error = computed(() => {
  if (statsQuery.error.value || papersQuery.error.value) return 'Failed to load stats.'
  return ''
})

const relatedOrder = [
  'authors',
  'institutions',
  'venues',
  'keywords',
  'tags',
  'years',
  'months',
  'summary_templates',
  'output_languages',
  'providers',
  'models',
  'prompt_templates',
  'translation_langs',
]

const labelKeys: Record<string, string> = {
  authors: 'coAuthors',
  institutions: 'institutions',
  venues: 'venues',
  keywords: 'keywords',
  tags: 'resources',
  years: 'years',
  months: 'months',
  summary_templates: 'summaryTemplate',
  output_languages: 'outputLang',
  providers: 'providers',
  models: 'models',
  prompt_templates: 'promptTemplate',
  translation_langs: 'translationLang',
}

function goFacet(targetFacet: string, value: string) {
  router.push(`/facet/${targetFacet}/${encodeURIComponent(value)}`)
}

const relatedExpanded = ref<Record<string, boolean>>({})

function toggleRelated(key: string) {
  relatedExpanded.value = { ...relatedExpanded.value, [key]: !relatedExpanded.value[key] }
}

watch([facet, facetValue], () => {
  page.value = 1
})

// Expanded summary states
const expanded = ref<Record<string, boolean>>({})
const expandedLoading = ref<Record<string, boolean>>({})
const expandedMarkdown = ref<Record<string, string>>({})

async function toggleSummary(item: { paper_id: string; summary?: string | null; is_short?: boolean }) {
  const id = item.paper_id
  const currently = expanded.value[id]
  if (currently) {
    expanded.value = { ...expanded.value, [id]: false }
    return
  }
  // If we already have expanded content, just show it
  if (expandedMarkdown.value[id]) {
    expanded.value = { ...expanded.value, [id]: true }
    return
  }
  // Try to use existing short summary first
  if (item.summary) {
    expandedMarkdown.value = { ...expandedMarkdown.value, [id]: item.summary }
    expanded.value = { ...expanded.value, [id]: true }
    return
  }
  // Fetch full summary
  expandedLoading.value = { ...expandedLoading.value, [id]: true }
  try {
    const result = await fetchJson<{ summary: string; is_short: boolean }>(`/api/v1/papers/${encodeURIComponent(id)}/summary`)
    expandedMarkdown.value = { ...expandedMarkdown.value, [id]: result.summary }
    expanded.value = { ...expanded.value, [id]: true }
  } catch {
    expandedMarkdown.value = { ...expandedMarkdown.value, [id]: '' }
  } finally {
    expandedLoading.value = { ...expandedLoading.value, [id]: false }
  }
}

// Snippet renderer - lazy loaded
const snippetRenderer = ref<(value: string) => string>((v) => v)

onMounted(async () => {
  const fn = await lazySnippet()
  // @ts-ignore
  snippetRenderer.value = (val: string) => String(fn(val))
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div class="text-xs uppercase text-ink-400">{{ t('facet') }}</div>
        <div class="text-xl font-semibold text-ink-900">{{ facet }}: {{ facetValue }}</div>
      </div>
      <div class="flex gap-2">
        <Button variant="outline" @click="router.push({ path: '/', query: { facet: facet, facet_id: facetValue } })">
          {{ t('browsePapers') }}
        </Button>
        <Button variant="outline" @click="router.push('/')">{{ t('backToSearch') }}</Button>
      </div>
    </div>

    <div v-if="error" class="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-600">
      {{ error }}
    </div>

    <Card>
      <CardHeader>
        <CardTitle class="text-sm">{{ t('overview') }}</CardTitle>
      </CardHeader>
      <CardContent>
        <div v-if="loadingStats" class="text-sm text-ink-500">{{ t('loading') }}</div>
        <div v-else-if="stats" class="flex flex-wrap gap-4 text-sm text-ink-600">
          <div class="rounded-lg border border-ink-100 bg-white px-3 py-2">
            {{ t('totalPapers') }}: <span class="font-semibold text-ink-900">{{ stats.total }}</span>
          </div>
        </div>
      </CardContent>
    </Card>

    <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Card v-for="key in relatedOrder" :key="key">
        <CardHeader>
          <CardTitle class="text-sm">{{ t(labelKeys[key] || key) }}</CardTitle>
        </CardHeader>
        <CardContent>
          <div v-if="loadingStats" class="text-sm text-ink-500">{{ t('loading') }}</div>
          <div v-else-if="stats && stats.related[key] && stats.related[key].length" class="space-y-3">
            <div class="flex flex-wrap gap-2">
            <Badge
              v-for="item in stats.related[key].slice(0, relatedExpanded[key] ? undefined : 20)"
              :key="item.value"
              role="button"
              tabindex="0"
              class="cursor-pointer"
              @click="goFacet(key, item.value)"
              @keydown.enter.prevent="goFacet(key, item.value)"
              @keydown.space.prevent="goFacet(key, item.value)"
            >
              {{ item.value }} ({{ item.paper_count }})
            </Badge>
            </div>
            <Button
              v-if="stats.related[key].length > 20"
              size="sm"
              variant="outline"
              @click="toggleRelated(key)"
            >
              {{ relatedExpanded[key] ? t('collapse') : t('showMore') }}
            </Button>
          </div>
          <div v-else class="text-sm text-ink-400">{{ t('noData') }}</div>
        </CardContent>
      </Card>
    </div>

    <Card>
      <CardHeader class="flex flex-row items-center justify-between">
        <CardTitle class="text-sm">{{ t('papers') }}</CardTitle>
        <div class="flex items-center gap-2">
          <Badge variant="secondary">
            {{ papers?.total ?? 0 }} {{ t('results') }}
          </Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div v-if="loadingPapers" class="space-y-3">
          <div class="h-16 animate-pulse rounded-xl bg-ink-100" />
          <div class="h-16 animate-pulse rounded-xl bg-ink-100" />
          <div class="h-16 animate-pulse rounded-xl bg-ink-100" />
        </div>
        <div v-else-if="papers" class="space-y-3">
          <SearchResultItem
            v-for="(item, index) in papers.items"
            :key="item.paper_id"
            :item="item"
            :display-index="(page - 1) * pageSize + index + 1"
            :is-selected="selection.selectedIds.has(item.paper_id)"
            :selection-full="selection.isFull"
            :expanded="expanded[item.paper_id]"
            :expanded-loading="expandedLoading[item.paper_id]"
            :expanded-markdown="expandedMarkdown[item.paper_id]"
            :snippet-renderer="snippetRenderer"
            :on-toggle-select="() => selection.toggle(item)"
            :on-toggle-summary="() => toggleSummary(item)"
          />
          <div class="flex items-center justify-between pt-4">
            <Button
              variant="outline"
              size="sm"
              :disabled="page <= 1"
              @click="page = Math.max(1, page - 1)"
            >
              {{ t('prev') }}
            </Button>
            <span class="text-sm text-ink-500">
              {{ t('pageInfo', { page: page, total: Math.ceil(papers.total / pageSize) }) }}
            </span>
            <Button
              variant="outline"
              size="sm"
              :disabled="!papers.has_more"
              @click="page = page + 1"
            >
              {{ t('next') }}
            </Button>
          </div>
        </div>
        <div v-else class="text-sm text-ink-400">{{ t('noData') }}</div>
      </CardContent>
    </Card>
  </div>
</template>
