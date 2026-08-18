import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDebounce } from '@vueuse/core'
import { useQuery, keepPreviousData, useQueryClient } from '@tanstack/vue-query'
import { DEFAULT_PAGE_SIZE, SEARCH_DEBOUNCE_MS } from '@/lib/config'
import {
  getFacet,
  getFacetByValuePapers,
  getFacetPapers,
  getStats,
  listPapers,
  searchPapers,
} from '@/lib/api'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'
import type { SearchResponse } from '@/types/api'

const BY_VALUE_FACETS = new Set([
  'summary_templates',
  'output_languages',
  'providers',
  'models',
  'prompt_templates',
  'translation_langs',
])

type SearchMode = 'query' | 'facet' | 'list'

interface SearchPageRequest {
  mode: SearchMode
  q: string
  page: number
  pageSize: number
  sort: string
  facet: string
  facetId: string
  facetByValue: boolean
}

function createSearchPageRequest(
  state: ReturnType<typeof useSearchState>,
  query: string,
): SearchPageRequest {
  const mode: SearchMode = query
    ? 'query'
    : state.facet.value && state.facetId.value
      ? 'facet'
      : 'list'

  return {
    mode,
    q: query,
    page: state.page.value,
    pageSize: state.pageSizeNum.value,
    sort: state.effectiveSort.value,
    facet: state.facet.value,
    facetId: state.facetId.value,
    facetByValue: state.facetByValue.value,
  }
}

function searchPageKey(request: SearchPageRequest) {
  return ['search', request] as const
}

async function fetchSearchPage(
  request: SearchPageRequest,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  if (request.mode === 'query') {
    return searchPapers(request.q, request.page, request.pageSize, request.sort, signal)
  }
  if (request.mode === 'facet') {
    if (request.facetByValue || BY_VALUE_FACETS.has(request.facet)) {
      return getFacetByValuePapers(
        request.facet,
        request.facetId,
        request.page,
        request.pageSize,
        signal,
      )
    }
    return getFacetPapers(
      request.facet,
      request.facetId,
      request.page,
      request.pageSize,
      signal,
    )
  }
  return listPapers(request.page, request.pageSize, request.sort, signal)
}

export function useSearchState() {
  const route = useRoute()
  const router = useRouter()
  const syncing = ref(false)

  const query = ref('')
  const page = ref(1)
  const pageSize = ref(String(DEFAULT_PAGE_SIZE))
  const facet = ref('')
  const facetId = ref('')
  const facetByValue = ref(false)
  const facetType = ref('authors')
  const facetPage = ref(1)
  const facetPageSize = ref(50)
  const facetSearch = ref('')
  const sortBy = ref('relevance')

  const pageSizeNum = computed(() => Number(pageSize.value) || DEFAULT_PAGE_SIZE)
  const effectiveSort = computed(() => {
    const base = sortBy.value || (query.value ? 'relevance' : 'year_desc')
    return query.value ? base : base === 'relevance' ? 'year_desc' : base
  })

  function syncFromRoute() {
    syncing.value = true
    if (route.name === 'facet' && route.params.facet && route.params.value) {
      facet.value = String(route.params.facet)
      facetId.value = String(route.params.value)
      // Usually metadata links are "by value" (name), but API treats them as ID if they are IDs.
      // For things like 'authors', the name is the ID.
      // For 'summary_templates', the template name is the ID.
      // So we don't strictly need facetByValue=true unless it's a special case.
      // Let's rely on standard ID lookup.
    } else {
      facet.value = typeof route.query.facet === 'string' ? route.query.facet : ''
      facetId.value = typeof route.query.facet_id === 'string' ? route.query.facet_id : ''
    }
    
    query.value = typeof route.query.q === 'string' ? route.query.q : ''
    page.value = route.query.page ? Number(route.query.page) || 1 : 1
    const pageSizeRaw = route.query.page_size ? Number(route.query.page_size) : DEFAULT_PAGE_SIZE
    pageSize.value = String(Number.isFinite(pageSizeRaw) && pageSizeRaw > 0 ? pageSizeRaw : DEFAULT_PAGE_SIZE)
    
    facetByValue.value = route.query.facet_by_value === '1'
    const sortRaw = typeof route.query.sort === 'string' ? route.query.sort : ''
    sortBy.value = sortRaw || (query.value ? 'relevance' : 'year_desc')
    facetType.value = facet.value || facetType.value
    syncing.value = false
  }

  function syncToRoute() {
    if (syncing.value) return
    const queryParams: Record<string, string> = {}
    if (query.value) queryParams.q = query.value
    if (page.value > 1) queryParams.page = String(page.value)
    if (pageSizeNum.value !== DEFAULT_PAGE_SIZE) queryParams.page_size = String(pageSizeNum.value)
    if (facet.value) queryParams.facet = facet.value
    if (facetId.value) queryParams.facet_id = facetId.value
    if (facetByValue.value) queryParams.facet_by_value = '1'
    if (sortBy.value) {
      if (query.value) {
        if (sortBy.value !== 'relevance') queryParams.sort = sortBy.value
      } else if (sortBy.value !== 'year_desc' && sortBy.value !== 'relevance') {
        queryParams.sort = sortBy.value
      }
    }
    router.replace({
      path: '/',
      query: queryParams,
    })
  }

  function setFacet(nextFacet: string, id: string, byValue = false) {
    facet.value = nextFacet
    facetId.value = id
    facetByValue.value = byValue
    page.value = 1
    query.value = ''
    syncToRoute()
  }

  function clearFacet() {
    facet.value = ''
    facetId.value = ''
    facetByValue.value = false
    page.value = 1
    syncToRoute()
  }

  function handleSearchInput() {
    page.value = 1
    facet.value = ''
    facetId.value = ''
    facetByValue.value = false
    syncToRoute()
  }

  return {
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
    effectiveSort,
    syncFromRoute,
    syncToRoute,
    setFacet,
    clearFacet,
    handleSearchInput,
  }
}

export function useSearchData(state: ReturnType<typeof useSearchState>) {
  const queryClient = useQueryClient()
  const debouncedQuery = useDebounce(state.query, SEARCH_DEBOUNCE_MS)
  const searchRequest = computed(() => createSearchPageRequest(state, debouncedQuery.value))

  const searchQuery = useQuery({
    queryKey: computed(() => searchPageKey(searchRequest.value)),
    queryFn: ({ queryKey, signal }) =>
      fetchSearchPage(queryKey[1] as SearchPageRequest, signal),
    placeholderData: keepPreviousData,
    staleTime: QUERY_CACHE_POLICY.search.staleTime,
    gcTime: QUERY_CACHE_POLICY.search.gcTime,
  })

  watch(
    [searchRequest, () => searchQuery.data.value, () => searchQuery.isPlaceholderData.value],
    ([request, response, isPlaceholderData]) => {
      if (!response || isPlaceholderData || response.page !== request.page) return

      const totalPages = Math.max(1, Math.ceil(response.total / Math.max(response.page_size, 1)))
      const adjacentPages = [request.page - 1, request.page + 1]
        .filter((page) => page >= 1 && page <= totalPages)

      for (const page of adjacentPages) {
        const adjacentRequest = { ...request, page }
        void queryClient.prefetchQuery({
          queryKey: searchPageKey(adjacentRequest),
          queryFn: ({ signal }) => fetchSearchPage(adjacentRequest, signal),
          staleTime: QUERY_CACHE_POLICY.search.staleTime,
          gcTime: QUERY_CACHE_POLICY.search.gcTime,
        }).catch(() => {
          // Prefetch is opportunistic; foreground navigation keeps its own error handling.
        })
      }
    },
    { flush: 'post' },
  )

  const statsQuery = useQuery({
    queryKey: ['stats'],
    queryFn: () => getStats(),
    staleTime: QUERY_CACHE_POLICY.stats.staleTime,
    gcTime: QUERY_CACHE_POLICY.stats.gcTime,
  })

  const facetQuery = useQuery({
    queryKey: computed(() => [
      'facet-list',
      {
        facet: state.facetType.value,
        page: state.facetPage.value,
        pageSize: state.facetPageSize.value,
      },
    ]),
    queryFn: () => getFacet(state.facetType.value, state.facetPage.value, state.facetPageSize.value),
    enabled: computed(() => Boolean(state.facetType.value)),
    staleTime: QUERY_CACHE_POLICY.stats.staleTime,
    gcTime: QUERY_CACHE_POLICY.stats.gcTime,
  })

  return { searchQuery, statsQuery, facetQuery }
}
