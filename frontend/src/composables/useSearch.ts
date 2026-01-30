import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useDebounce } from '@vueuse/core'
import { useQuery, keepPreviousData } from '@tanstack/vue-query'
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

const BY_VALUE_FACETS = new Set([
  'summary_templates',
  'output_languages',
  'providers',
  'models',
  'prompt_templates',
  'translation_langs',
])

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
  const debouncedQuery = useDebounce(state.query, SEARCH_DEBOUNCE_MS)

  const mode = computed(() => {
    if (debouncedQuery.value) return 'query'
    if (state.facet.value && state.facetId.value) return 'facet'
    return 'list'
  })

  const searchQuery = useQuery({
    queryKey: computed(() => [
      'search',
      {
        mode: mode.value,
        q: debouncedQuery.value,
        page: state.page.value,
        pageSize: state.pageSizeNum.value,
        sort: state.effectiveSort.value,
        facet: state.facet.value,
        facetId: state.facetId.value,
        facetByValue: state.facetByValue.value,
      },
    ]),
    queryFn: () => {
      if (mode.value === 'query') {
        return searchPapers(
          debouncedQuery.value,
          state.page.value,
          state.pageSizeNum.value,
          state.effectiveSort.value
        )
      }
      if (mode.value === 'facet') {
        if (state.facetByValue.value || BY_VALUE_FACETS.has(state.facet.value)) {
          return getFacetByValuePapers(
            state.facet.value,
            state.facetId.value,
            state.page.value,
            state.pageSizeNum.value
          )
        }
        return getFacetPapers(
          state.facet.value,
          state.facetId.value,
          state.page.value,
          state.pageSizeNum.value
        )
      }
      return listPapers(state.page.value, state.pageSizeNum.value, state.effectiveSort.value)
    },
    placeholderData: keepPreviousData,
    staleTime: QUERY_CACHE_POLICY.search.staleTime,
    gcTime: QUERY_CACHE_POLICY.search.gcTime,
  })

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
