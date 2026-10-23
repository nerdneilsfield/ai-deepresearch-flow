<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSelectionStore } from '@/stores/selection'
import { useFavoriteStore } from '@/stores/favorites'
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
import { Skeleton } from '@/components/ui/skeleton'
import { PaginationJump } from '@/components/ui/pagination'
import SearchResultItem from '@/components/search/SearchResultItem.vue'
import AdvancedSearchPanel from '@/components/AdvancedSearchPanel.vue'
import SearchChips from '@/components/search/SearchChips.vue'
import SearchAdvancedForm from '@/components/search/SearchAdvancedForm.vue'
import SearchEmptyState from '@/components/search/SearchEmptyState.vue'
import {
  appendCondition,
  freeTerms,
  parseSearchExpression,
  removeToken,
  toSemanticQuery,
  type ExprToken,
} from '@/lib/search-expression'
import AdvancedSearchResults from '@/components/AdvancedSearchResults.vue'
import {
  advancedSearch,
  AdvancedSearchHTTPError,
  type AdvancedSearchParams,
  type AdvancedSearchResult,
} from '@/lib/api'
import type { SearchItem } from '@/types/api'
import type { FavoriteRating } from '@/types/favorites'
import { useAdvancedSearchAuth } from '@/composables/useAdvancedSearchAuth'
import { BarChart2, SlidersHorizontal, X } from 'lucide-vue-next'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
// Desktop shows the sidebar expanded; narrower screens start collapsed.
const sidebarOpen = ref(globalThis.matchMedia?.('(min-width: 1024px)').matches ?? true)
const selection = useSelectionStore()
const favorites = useFavoriteStore()
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
  mode,
  // effectiveSort is computed but not used in template
  effectiveSort: _effectiveSort,
  syncFromRoute,
  syncToRoute,
  setFacet,
  clearFacet,
  handleSearchInput,
} = searchState

const { searchQuery, statsQuery, facetQuery } = useSearchData(searchState)
const {
  token: advancedToken,
  authenticated: advancedAuthenticated,
  refreshAfterFailure,
} = useAdvancedSearchAuth()

const results = computed(() => searchQuery.data.value ?? null)
// Offer a one-click title-scoped version of a plain free-text query (no field: syntax yet).
const titleScopeQuery = computed(() => {
  const q = query.value.trim()
  if (!q || /\w+:/.test(q) || q.startsWith('-')) return ''
  return /^".*"$/.test(q) ? `title:${q}` : `title:"${q}"`
})
const stats = computed(() => statsQuery.data.value ?? null)
const facetList = computed(() => facetQuery.data.value ?? null)
const loading = computed(() => searchQuery.isFetching.value)
const error = computed(() => {
  const err = searchQuery.error.value
  if (!err) return ''
  try {
    const body = JSON.parse(err.message) as { error?: string; detail?: string }
    if (body.error === 'invalid_query') return t('searchInvalidQuery', { detail: body.detail ?? '' })
  } catch {
    // Not a JSON error body: fall through to the generic message.
  }
  return 'Failed to fetch results. Please retry.'
})

const parsedQuery = computed(() => parseSearchExpression(query.value))
const semanticQuery = computed(() => toSemanticQuery(parsedQuery.value))
const highlightTerms = computed(() => freeTerms(parsedQuery.value.freeText))
const showAdvancedForm = ref(false)
const semanticSearched = ref(false)
const semanticAvailable = computed(() => advancedAuthenticated.value)
// Labels of the conditions the user could drop when nothing matches.
const chipHints = computed(() =>
  parsedQuery.value.tokens.map((token) => query.value.slice(token.start, token.end)),
)

const snippetRenderer = ref<(value: string) => string>((v) => v)
const { expanded, expandedMarkdown, expandedLoading, toggleSummary } = useExpandableSummary()

function facetName(name: string) {
  const labels: Record<string, string> = {
    authors: t('authors'),
    venues: t('venues'),
    keywords: t('keywords'),
    institutions: t('institutions'),
    tags: t('resources'),
    years: t('years'),
    months: t('months'),
  }
  return labels[name] || name
}
const facetLabel = computed(() => facetName(facetType.value))

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
const advancedResults = ref<AdvancedSearchResult[]>([])
const advancedDegraded = ref(false)
const advancedDegradationReason = ref<string | null>(null)
const advancedDegradationMessage = ref<string | null>(null)
const advancedSearching = ref(false)

function forceSearch() {
  syncToRoute()
  if (mode.value === 'semantic') {
    void runSemanticSearch()
  } else {
    searchQuery.refetch()
  }
}

async function runSemanticSearch() {
  if (!advancedAuthenticated.value) return
  const { q, filters } = semanticQuery.value
  if (!q.trim()) {
    ui.pushToast(t('semanticNeedsText'), 'warning')
    return
  }
  semanticSearched.value = true
  await onAdvancedSearch({ q, filters })
}

function setMode(next: 'keyword' | 'semantic') {
  if (next === mode.value) return
  if (next === 'semantic' && !semanticAvailable.value) return
  mode.value = next
  syncToRoute()
  if (next === 'semantic' && query.value.trim()) void runSemanticSearch()
}

function onRemoveChip(token: ExprToken) {
  query.value = removeToken(query.value, token)
  handleSearchInput()
  forceSearch()
}

function onApplyAdvancedForm(expression: string) {
  query.value = expression
  showAdvancedForm.value = false
  handleSearchInput()
  forceSearch()
}

// Authors and years stack as expression conditions; other facets keep the single-facet filter.
function onFacetItem(id: string, value: string) {
  if (facetType.value === 'authors') addCondition('author', value)
  else if (facetType.value === 'years') addCondition('year', value)
  else setFacet(facetType.value, id)
}

function addCondition(field: 'author' | 'year', value: string) {
  query.value = appendCondition(query.value, field, value)
  handleSearchInput()
  forceSearch()
}

async function onAdvancedSearch(params: AdvancedSearchParams) {
  if (!advancedAuthenticated.value) return
  advancedSearching.value = true
  try {
    const body = await advancedSearch(params, advancedToken.value)
    advancedResults.value = body.results
    advancedDegraded.value = body.degraded
    advancedDegradationReason.value = body.degradation?.reason ?? null
    advancedDegradationMessage.value = body.degradation?.message ?? null
    if (body.degraded) {
      ui.pushToast(
        body.degradation?.message ?? `Advanced search degraded: ${body.degradation?.reason ?? 'unknown'}`,
        'warning',
      )
    }
  } catch (error) {
    advancedResults.value = []
    advancedDegraded.value = false
    advancedDegradationReason.value = null
    advancedDegradationMessage.value = null
    if (error instanceof AdvancedSearchHTTPError) {
      if (error.status === 401) {
        await refreshAfterFailure()
        ui.pushToast(t('advancedAuthExpired'), 'error')
      } else if (error.status === 400) {
        ui.pushToast(`Invalid request: ${error.message}`, 'error')
      } else if (error.status === 503) {
        ui.pushToast('Advanced search temporarily unavailable.', 'error')
      } else {
        ui.pushToast(`Advanced search failed (${error.status}).`, 'error')
      }
    } else {
      ui.pushToast('Advanced search failed. Please try again.', 'error')
    }
  } finally {
    advancedSearching.value = false
  }
}

function advancedResultToSearchItem(result: AdvancedSearchResult): SearchItem {
  return {
    paper_id: result.paper_id,
    title: result.paper.title,
    year: result.paper.year,
    venue: result.paper.venue,
    authors: result.paper.authors,
    summary_preview: result.chunk.text,
    preferred_summary_template: result.chunk.template_tag || undefined,
    has_pdf: false,
    has_source: false,
    has_translated: false,
    pdf_url: null,
    source_md_url: null,
    translated_md_urls: {},
    summary_url: undefined,
    manifest_url: undefined,
  }
}

function onAdvancedToggleSelect(result: AdvancedSearchResult) {
  void selection.toggle(advancedResultToSearchItem(result))
}

function onAdvancedToggleFavorite(result: AdvancedSearchResult) {
  void favorites.toggle(advancedResultToSearchItem(result))
}

function onAdvancedSetFavoriteRating(result: AdvancedSearchResult, rating: FavoriteRating) {
  void favorites.setRating(result.paper_id, rating)
}

// A shared ?mode=semantic URL can only run once the visitor is authenticated.
watch(
  [advancedAuthenticated, mode],
  ([authed, current]) => {
    if (authed && current === 'semantic' && query.value.trim() && !semanticSearched.value) {
      void runSemanticSearch()
    }
  },
)

watch(
  () => route.query,
  () => {
    syncFromRoute()
  }
)

watch([query, page, pageSize, facet, facetId, facetByValue, sortBy, mode], () => {
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
  <div class="grid grid-cols-1 items-start gap-6 lg:grid-cols-[260px_minmax(0,_1fr)]">
    <!-- Below lg the sidebar follows the results and stays collapsed; the search box must be first on phones. -->
    <details
      class="order-last space-y-4 lg:order-none"
      :open="sidebarOpen"
      @toggle="sidebarOpen = ($event.target as HTMLDetailsElement).open"
    >
      <summary class="cursor-pointer rounded-md border border-border px-3 py-2 text-sm font-medium text-foreground lg:hidden">
        {{ t('statsAndFilters') }}
      </summary>
      <div class="mt-4 space-y-4 lg:mt-0">
      <Card>
        <CardHeader>
          <CardTitle class="text-sm">{{ t('stats') }}</CardTitle>
        </CardHeader>
        <CardContent v-if="stats" class="space-y-2 text-xs text-ink-600">
          <div class="flex items-baseline justify-between gap-2">
            <span class="text-muted-foreground">{{ t('total') }}</span>
            <span class="font-display-serif text-2xl font-semibold leading-none text-primary">{{ stats.total.toLocaleString() }}</span>
          </div>
          <div class="mt-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground/70">{{ t('topAuthors') }}</div>
          <div class="space-y-1">
            <button
              v-for="author in stats.authors.slice(0, 6)"
              :key="author.value"
              class="flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-ink-600 hover:bg-ink-100"
              type="button"
              :aria-label="`Filter by author ${author.value}`"
              @click="addCondition('author', String(author.value))"
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
              @click="addCondition('year', String(year.value))"
              @keydown.enter.prevent="addCondition('year', String(year.value))"
              @keydown.space.prevent="addCondition('year', String(year.value))"
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
                  @click="onFacetItem(String(item.id), String(item.value))"
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
                @click="onFacetItem(String(item.id), String(item.value))"
              >
                <span class="truncate">{{ item.value }}</span>
                <span>{{ item.paper_count }}</span>
              </button>
            </div>
          </div>
          <div
            v-if="facetList && facetList.total > facetPageSize && facetSearch.trim().length === 0"
            class="flex flex-wrap items-center justify-between gap-1 pt-2 text-xs text-ink-500"
          >
            <Button variant="ghost" size="sm" :disabled="facetPage <= 1" @click="facetPage = Math.max(1, facetPage - 1)">
              {{ t('prev') }}
            </Button>
            <div class="flex items-center gap-2">
              <span>{{ t('pageInfo', { page: facetPage, total: Math.ceil(facetList.total / facetPageSize) }) }}</span>
              <PaginationJump
                v-model="facetPage"
                :total-pages="Math.ceil(facetList.total / facetPageSize)"
              />
            </div>
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
      </div>
    </details>

    <section class="min-w-0 flex-1 space-y-4">
      <Card class="mt-2 shadow-elevated">
        <CardContent class="space-y-3 pt-4">
          <form class="flex flex-col gap-3 sm:flex-row sm:items-center" role="search" @submit.prevent="forceSearch">
            <Input
              v-model="query"
              class="h-11 text-base"
              :placeholder="t('searchPlaceholder')"
              aria-label="Search papers"
              data-testid="search-input"
              @input="handleSearchInput"
            />
            <Button type="submit" class="h-11 shrink-0" data-testid="search-submit">{{ t('search') }}</Button>
          </form>

          <div class="flex flex-wrap items-center gap-2">
            <TooltipProvider>
              <div
                class="inline-flex items-center rounded-lg bg-muted p-1 text-muted-foreground"
                role="radiogroup"
                :aria-label="t('searchMode')"
                data-testid="search-mode"
              >
                <button
                  type="button"
                  role="radio"
                  :aria-checked="mode === 'keyword'"
                  class="rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  :class="mode === 'keyword' ? 'bg-background text-foreground shadow' : 'hover:text-foreground'"
                  data-testid="search-mode-keyword"
                  @click="setMode('keyword')"
                >
                  {{ t('modeKeyword') }}
                </button>
                <Tooltip :disabled="semanticAvailable">
                  <TooltipTrigger as-child>
                    <span :tabindex="semanticAvailable ? undefined : 0">
                      <button
                        type="button"
                        role="radio"
                        :aria-checked="mode === 'semantic'"
                        :aria-disabled="!semanticAvailable"
                        :disabled="!semanticAvailable"
                        class="rounded-md px-3 py-1 text-sm font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                        :class="mode === 'semantic' ? 'bg-background text-foreground shadow' : 'hover:text-foreground'"
                        data-testid="search-mode-semantic"
                        @click="setMode('semantic')"
                      >
                        {{ t('modeSemantic') }}
                      </button>
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>{{ t('semanticNeedsAuthTip') }}</TooltipContent>
                </Tooltip>
              </div>
            </TooltipProvider>
            <Button
              type="button"
              variant="outline"
              class="gap-1.5"
              :aria-expanded="showAdvancedForm"
              data-testid="search-advanced-toggle"
              @click="showAdvancedForm = !showAdvancedForm"
            >
              <SlidersHorizontal />
              <span>{{ t('searchAdvancedForm') }}</span>
            </Button>
            <div class="ml-auto">
              <AdvancedSearchPanel />
            </div>
          </div>

          <SearchAdvancedForm
            v-if="showAdvancedForm"
            @apply="onApplyAdvancedForm"
            @cancel="showAdvancedForm = false"
          />

          <SearchChips
            :tokens="parsedQuery.tokens"
            :mode="mode"
            :ignored="semanticQuery.ignored"
            @remove="onRemoveChip"
          />

          <div v-if="mode === 'keyword'" class="flex flex-wrap items-center gap-3 text-xs text-ink-500">
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
          <div v-if="facet && facetId" class="flex flex-wrap items-center gap-2 text-xs text-ink-500" data-testid="facet-filter">
            <span>{{ t('facetFilter') }}</span>
            <Badge variant="navy" class="gap-1 pr-1">
              {{ t('facetChip', { facet: facetName(facet), value: facetId }) }}
              <button
                type="button"
                class="inline-flex h-4 w-4 items-center justify-center rounded-full hover:bg-black/10 dark:hover:bg-white/15"
                :aria-label="t('chipRemove', { label: t('facetChip', { facet: facetName(facet), value: facetId }) })"
                data-testid="facet-chip-remove"
                @click="clearFacet"
              >
                <X class="h-3 w-3" />
              </button>
            </Badge>
            <Button size="icon-sm" variant="ghost" class="h-5 w-5" title="View Stats" @click="router.push(`/facet/${facet}/${encodeURIComponent(facetId)}`)">
              <BarChart2 class="h-3 w-3" />
            </Button>
          </div>
          <div class="text-xs text-ink-400">
            {{ t('shareTip') }}
          </div>
        </CardContent>
      </Card>

      <template v-if="mode === 'semantic'">
        <div
          v-if="!semanticAvailable"
          class="rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800 dark:border-amber-500/40 dark:bg-amber-950/40 dark:text-amber-200"
          data-testid="semantic-auth-notice"
        >
          {{ t('semanticNeedsAuth') }}
        </div>
        <div v-if="advancedSearching" class="space-y-3" role="status" aria-live="polite">
          <Skeleton v-for="n in 3" :key="n" class="h-28 rounded-xl" />
        </div>
        <AdvancedSearchResults
          v-else-if="semanticSearched || advancedResults.length"
          :results="advancedResults"
          :degraded="advancedDegraded"
          :degradation-reason="advancedDegradationReason"
          :degradation-message="advancedDegradationMessage"
          :selected-ids="selection.selectedIds"
          :selection-full="selection.isFull"
          :favorite-ids="favorites.favoriteIds"
          :favorite-ratings="favorites.ratingsById"
          :highlight-terms="highlightTerms"
          :removable-hints="chipHints"
          @toggle-select="onAdvancedToggleSelect"
          @toggle-favorite="onAdvancedToggleFavorite"
          @set-favorite-rating="onAdvancedSetFavoriteRating"
        />
      </template>

      <div v-if="mode === 'keyword' && loading" class="space-y-3" role="status" aria-live="polite">
        <Skeleton v-for="n in 5" :key="n" class="h-28 rounded-xl" />
      </div>
      <div
        v-else-if="mode === 'keyword' && error"
        class="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-600"
        role="status"
      >
        {{ error }}
      </div>

      <div v-if="mode === 'keyword' && results" class="space-y-3" :aria-busy="loading">
        <div class="text-xs text-ink-500" aria-live="polite">
          {{ t('showingResults', { start: startResult, end: endResult, total: results.total }) }}
          <template v-if="titleScopeQuery">
            · <button type="button" class="text-primary underline" data-testid="scope-title" @click="query = titleScopeQuery">{{ t('searchTitleOnly') }}</button>
          </template>
          <template v-if="sortBy !== 'relevance' && query">
            · <button type="button" class="text-primary underline" @click="sortBy = 'relevance'">{{ t('sortByRelevance') }}</button>
          </template>
        </div>
        <SearchEmptyState v-if="results.total === 0 && !loading" :hints="chipHints" />
        <SearchResultItem
          v-for="(item, index) in results.items"
          :key="item.paper_id"
          :item="item"
          :display-index="item.paper_index || (page - 1) * pageSizeNum + index + 1"
          :is-selected="selection.selectedIds.has(item.paper_id)"
          :selection-full="selection.isFull"
          :is-favorite="favorites.favoriteIds.has(item.paper_id)"
          :favorite-rating="favorites.ratingFor(item.paper_id)"
          :expanded="expanded[item.paper_id]"
          :expanded-markdown="expandedMarkdown[item.paper_id]"
          :expanded-loading="expandedLoading[item.paper_id]"
          :snippet-renderer="snippetRenderer"
          @toggle-select="selection.toggle(item)"
          @toggle-favorite="favorites.toggle(item)"
          @set-favorite-rating="favorites.setRating(item.paper_id, $event)"
          @toggle-summary="toggleSummary(item)"
        />

        <div class="flex flex-wrap items-center justify-between gap-2 pt-2 text-sm text-ink-500">
          <Button variant="ghost" size="sm" :disabled="page <= 1" @click="page = Math.max(1, page - 1)">
            {{ t('prev') }}
          </Button>
          <div class="flex items-center gap-2">
            <span>{{ t('pageInfo', { page: page, total: totalPages }) }}</span>
            <PaginationJump v-model="page" :total-pages="totalPages" />
          </div>
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
