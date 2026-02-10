<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
import { lazySnippet } from '@/lib/lazy'
import { useSearchData, useSearchState } from '@/composables/useSearch'
import { useExpandableSummary } from '@/composables/useExpandableSummary'
import { RecycleScroller } from 'vue-virtual-scroller'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import SearchResultItem from '@/components/search/SearchResultItem.vue'
import { BarChart2 } from 'lucide-vue-next'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const selection = useSelectionStore()
const ui = useUiStore()

const searchState = useSearchState()
const {
  query,
  page,
  pageSize,
  pageSizeNum,
  facet,
  facetId,
  facetByValue,
  facetType,
  facetPage,
  facetPageSize,
  facetSearch,
  sortBy,
  // effectiveSort is computed but not used in template
  effectiveSort: _effectiveSort,
  syncFromRoute,
  syncToRoute,
  setFacet,
  clearFacet,
  handleSearchInput,
} = searchState

const { searchQuery, statsQuery, facetQuery } = useSearchData(searchState)

const results = computed(() => searchQuery.data.value ?? null)
const stats = computed(() => statsQuery.data.value ?? null)
const facetList = computed(() => facetQuery.data.value ?? null)
const loading = computed(() => searchQuery.isFetching.value)
const error = computed(() => (searchQuery.error.value ? 'Failed to fetch results. Please retry.' : ''))

const snippetRenderer = ref<(value: string) => string>((v) => v)
const { expanded, expandedMarkdown, expandedLoading, toggleSummary } = useExpandableSummary()

const facetLabel = computed(() => {
  const labels: Record<string, string> = {
    authors: t('authors'),
    venues: t('venues'),
    keywords: t('keywords'),
    institutions: t('institutions'),
    tags: t('resources'),
    years: t('years'),
    months: t('months'),
  }
  return labels[facetType.value] || facetType.value
})

const startResult = computed(() => (page.value - 1) * pageSizeNum.value + 1)
const endResult = computed(() => Math.min(page.value * pageSizeNum.value, results.value?.total || 0))

const filteredFacetItems = computed(() => {
  if (!facetList.value) return []
  const needle = facetSearch.value.trim().toLowerCase()
  if (!needle) return facetList.value.items
  return facetList.value.items.filter((item) => String(item.value).toLowerCase().includes(needle))
})

const totalPages = computed(() => {
  if (!results.value) return 1
  return Math.max(1, Math.ceil(results.value.total / results.value.page_size))
})

function forceSearch() {
  syncToRoute()
  searchQuery.refetch()
}

watch(
  () => route.query,
  () => {
    syncFromRoute()
  }
)

watch([query, page, pageSize, facet, facetId, facetByValue, sortBy], () => {
  syncToRoute()
})

watch(query, () => {
  if (!query.value && sortBy.value === 'relevance') {
    sortBy.value = 'year_desc'
  }
})

watch(pageSize, () => {
  page.value = 1
})

watch(sortBy, () => {
  page.value = 1
})

watch(facetType, () => {
  facetPage.value = 1
  facetSearch.value = ''
})

onMounted(async () => {
  syncFromRoute()
  const fn = await lazySnippet()
  // @ts-ignore
  snippetRenderer.value = (val: string) => String(fn(val))
})

watch(statsQuery.error, (err) => {
  if (err) ui.pushToast('Failed to load stats', 'error')
})

watch(facetQuery.error, (err) => {
  if (err) ui.pushToast('Failed to load facet list', 'error')
})
</script>

<template>
  <div class="grid grid-cols-[280px_minmax(0,_1fr)] items-start gap-6 max-sm:grid-cols-1">
    <aside class="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle class="text-sm">{{ t('stats') }}</CardTitle>
        </CardHeader>
        <CardContent v-if="stats" class="space-y-2 text-xs text-ink-600">
          <div class="flex items-center justify-between">
            <span>{{ t('total') }}</span>
            <span class="font-semibold text-ink-900">{{ stats.total }}</span>
          </div>
          <div class="mt-3 text-xs font-semibold uppercase text-ink-400">{{ t('topAuthors') }}</div>
          <div class="space-y-1">
            <button
              v-for="author in stats.authors.slice(0, 6)"
              :key="author.value"
              class="flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-ink-600 hover:bg-ink-100"
              type="button"
              :aria-label="`Filter by author ${author.value}`"
              @click="setFacet('authors', String(author.value), true)"
            >
              <span class="truncate">{{ author.value }}</span>
              <span>{{ author.paper_count }}</span>
            </button>
          </div>
          <div class="mt-3 text-xs font-semibold uppercase text-ink-400">{{ t('years') }}</div>
          <div class="flex flex-wrap gap-2">
            <Badge
              v-for="year in stats.years.slice(0, 8)"
              :key="year.value"
              role="button"
              tabindex="0"
              class="cursor-pointer"
              :aria-label="`Filter by year ${year.value}`"
              @click="setFacet('years', String(year.value), true)"
              @keydown.enter.prevent="setFacet('years', String(year.value), true)"
              @keydown.space.prevent="setFacet('years', String(year.value), true)"
            >
              {{ year.value }}
            </Badge>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle class="text-sm">{{ t('facetBrowser') }}</CardTitle>
        </CardHeader>
        <CardContent class="space-y-2">
          <Select v-model="facetType">
            <SelectTrigger class="w-full">
              <SelectValue :placeholder="t('selectFacet')" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="authors">{{ t('authors') }}</SelectItem>
              <SelectItem value="venues">{{ t('venues') }}</SelectItem>
              <SelectItem value="keywords">{{ t('keywords') }}</SelectItem>
              <SelectItem value="institutions">{{ t('institutions') }}</SelectItem>
            <SelectItem value="tags">{{ t('resources') }}</SelectItem>
            <SelectItem value="years">{{ t('years') }}</SelectItem>
            <SelectItem value="months">{{ t('months') }}</SelectItem>
            <SelectItem value="summary_templates">{{ t('summaryTemplates') }}</SelectItem>
            <SelectItem value="output_languages">{{ t('outputLanguages') }}</SelectItem>
            <SelectItem value="providers">{{ t('providers') }}</SelectItem>
            <SelectItem value="models">{{ t('models') }}</SelectItem>
            <SelectItem value="prompt_templates">{{ t('promptTemplates') }}</SelectItem>
            <SelectItem value="translation_langs">{{ t('translationLanguages') }}</SelectItem>
          </SelectContent>
        </Select>
          <Input
            v-model="facetSearch"
            :placeholder="t('filterFacet')"
            aria-label="Filter facet values"
          />
          <Separator />
          <div class="text-xs uppercase text-ink-400">{{ facetLabel }}</div>
          <div v-if="facetList" class="max-h-56 overflow-auto">
            <RecycleScroller
              v-if="filteredFacetItems.length > 50"
              :items="filteredFacetItems"
              :item-size="32"
              class="space-y-1"
            >
              <template #default="{ item }">
                <button
                  class="flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-ink-600 hover:bg-ink-100"
                  type="button"
                  :aria-label="`Filter by ${facetLabel} ${item.value}`"
                  @click="setFacet(facetType, String(item.id))"
                >
                  <span class="truncate">{{ item.value }}</span>
                  <span>{{ item.paper_count }}</span>
                </button>
              </template>
            </RecycleScroller>
            <div v-else class="space-y-1">
              <button
                v-for="item in filteredFacetItems"
                :key="item.value"
                class="flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-ink-600 hover:bg-ink-100"
                type="button"
                :aria-label="`Filter by ${facetLabel} ${item.value}`"
                @click="setFacet(facetType, String(item.id))"
              >
                <span class="truncate">{{ item.value }}</span>
                <span>{{ item.paper_count }}</span>
              </button>
            </div>
          </div>
          <div
            v-if="facetList && facetList.total > facetPageSize && facetSearch.trim().length === 0"
            class="flex items-center justify-between pt-2 text-xs text-ink-500"
          >
            <Button variant="ghost" size="sm" :disabled="facetPage <= 1" @click="facetPage = Math.max(1, facetPage - 1)">
              {{ t('prev') }}
            </Button>
            <span>{{ t('pageInfo', { page: facetPage, total: Math.ceil(facetList.total / facetPageSize) }) }}</span>
            <Button
              variant="ghost"
              size="sm"
              :disabled="facetPage >= Math.ceil(facetList.total / facetPageSize)"
              @click="facetPage = Math.min(Math.ceil(facetList.total / facetPageSize), facetPage + 1)"
            >
              {{ t('next') }}
            </Button>
          </div>
        </CardContent>
      </Card>
    </aside>

    <section class="min-w-0 flex-1 space-y-4">
      <Card class="mt-2">
        <CardContent class="pt-4">
          <form class="flex flex-col gap-3 sm:flex-row sm:items-center" role="search" @submit.prevent="forceSearch">
            <Input
              v-model="query"
              :placeholder="t('searchPlaceholder')"
              aria-label="Search papers"
              @input="handleSearchInput"
            />
            <Button type="submit" variant="outline">Search</Button>
          </form>
          <div class="mt-3 flex flex-wrap items-center gap-3 text-xs text-ink-500">
            <span class="font-semibold text-ink-700">{{ t('sort') }}</span>
            <Select v-model="sortBy">
              <SelectTrigger class="h-8 w-[200px]">
                <SelectValue :placeholder="t('sort')" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="relevance" :disabled="!query">{{ t('relevance') }}</SelectItem>
                <SelectItem value="year_desc">{{ t('yearDesc') }}</SelectItem>
                <SelectItem value="year_asc">{{ t('yearAsc') }}</SelectItem>
                <SelectItem value="title_asc">{{ t('titleAsc') }}</SelectItem>
                <SelectItem value="title_desc">{{ t('titleDesc') }}</SelectItem>
                <SelectItem value="venue_asc">{{ t('venueAsc') }}</SelectItem>
                <SelectItem value="venue_desc">{{ t('venueDesc') }}</SelectItem>
              </SelectContent>
            </Select>
            <span class="font-semibold text-ink-700">{{ t('pageSize') }}</span>
            <Select v-model="pageSize">
              <SelectTrigger class="h-8 w-[120px]">
                <SelectValue :placeholder="t('pageSize')" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="10">10</SelectItem>
                <SelectItem value="20">20</SelectItem>
                <SelectItem value="50">50</SelectItem>
                <SelectItem value="100">100</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div v-if="facet && facetId" class="mt-3 flex items-center gap-2 text-xs text-ink-500">
            <span>{{ t('facetFilter') }}</span>
            <Badge>{{ facet }}: {{ facetId }}</Badge>
            <Button size="icon-sm" variant="ghost" class="h-5 w-5" title="View Stats" @click="router.push(`/facet/${facet}/${encodeURIComponent(facetId)}`)">
              <BarChart2 class="h-3 w-3" />
            </Button>
            <button class="text-xs text-accent-600" type="button" @click="clearFacet">{{ t('clear') }}</button>
          </div>
          <div class="mt-3 text-xs text-ink-400">
            {{ t('shareTip') }}
          </div>
        </CardContent>
      </Card>

      <div v-if="loading" class="rounded-xl border border-ink-100 bg-white p-6 text-sm text-ink-500" role="status">
        {{ t('loading') }}
      </div>
      <div
        v-else-if="error"
        class="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-600"
        role="status"
      >
        {{ error }}
      </div>

      <div v-if="results" class="space-y-3" :aria-busy="loading">
        <div class="text-xs text-ink-500" aria-live="polite">
          {{ t('showingResults', { start: startResult, end: endResult, total: results.total }) }}
        </div>
        <SearchResultItem
          v-for="(item, index) in results.items"
          :key="item.paper_id"
          :item="item"
          :display-index="item.paper_index || (page - 1) * pageSizeNum + index + 1"
          :is-selected="selection.selectedIds.has(item.paper_id)"
          :selection-full="selection.isFull"
          :expanded="expanded[item.paper_id]"
          :expanded-markdown="expandedMarkdown[item.paper_id]"
          :expanded-loading="expandedLoading[item.paper_id]"
          :snippet-renderer="snippetRenderer"
          @toggle-select="selection.toggle(item)"
          @toggle-summary="toggleSummary(item)"
        />

        <div class="flex items-center justify-between pt-2 text-sm text-ink-500">
          <Button variant="ghost" size="sm" :disabled="page <= 1" @click="page = Math.max(1, page - 1)">
            {{ t('prev') }}
          </Button>
          <span>{{ t('pageInfo', { page: page, total: totalPages }) }}</span>
          <Button
            variant="ghost"
            size="sm"
            :disabled="page >= totalPages"
            @click="page = Math.min(totalPages, page + 1)"
          >
            {{ t('next') }}
          </Button>
        </div>
      </div>
    </section>
  </div>
</template>
