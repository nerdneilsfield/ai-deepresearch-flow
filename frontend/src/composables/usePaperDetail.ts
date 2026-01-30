import { computed, type Ref } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { getPaperDetail } from '@/lib/api'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'

export function usePaperDetail(paperId: Ref<string>) {
  const detailQuery = useQuery({
    queryKey: computed(() => ['paper-detail', paperId.value]),
    queryFn: () => getPaperDetail(paperId.value),
    enabled: computed(() => Boolean(paperId.value)),
    staleTime: QUERY_CACHE_POLICY.detail.staleTime,
    gcTime: QUERY_CACHE_POLICY.detail.gcTime,
  })

  return { detailQuery }
}

