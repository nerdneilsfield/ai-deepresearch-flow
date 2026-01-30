import { computed, type Ref } from 'vue'
import { useQuery, keepPreviousData } from '@tanstack/vue-query'
import { getFacetByValuePapers, getFacetByValueStats } from '@/lib/api'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'

export function useFacetStats(facet: Ref<string>, value: Ref<string>, page: Ref<number>, pageSize: Ref<number>) {
  const statsQuery = useQuery({
    queryKey: computed(() => ['facet-stats', facet.value, value.value]),
    queryFn: () => getFacetByValueStats(facet.value, value.value),
    enabled: computed(() => Boolean(facet.value && value.value)),
    staleTime: QUERY_CACHE_POLICY.stats.staleTime,
    gcTime: QUERY_CACHE_POLICY.stats.gcTime,
  })

  const papersQuery = useQuery({
    queryKey: computed(() => [
      'facet-papers',
      { facet: facet.value, value: value.value, page: page.value, pageSize: pageSize.value },
    ]),
    queryFn: () => getFacetByValuePapers(facet.value, value.value, page.value, pageSize.value),
    enabled: computed(() => Boolean(facet.value && value.value)),
    placeholderData: keepPreviousData,
    staleTime: QUERY_CACHE_POLICY.search.staleTime,
    gcTime: QUERY_CACHE_POLICY.search.gcTime,
  })

  return { statsQuery, papersQuery }
}
