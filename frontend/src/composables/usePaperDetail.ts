import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { getPaperDetailCached } from '@/lib/api'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'

export function usePaperDetail(paperId: Ref<string>) {
  const queryClient = useQueryClient()
  const queryKey = computed(() => ['paper-detail', paperId.value])
  const detailQuery = useQuery({
    queryKey,
    queryFn: () =>
      getPaperDetailCached(paperId.value, {
        onRevalidated: (detail) => {
          queryClient.setQueryData(queryKey.value, detail)
        },
      }),
    enabled: computed(() => Boolean(paperId.value)),
    staleTime: QUERY_CACHE_POLICY.detail.staleTime,
    gcTime: QUERY_CACHE_POLICY.detail.gcTime,
  })

  return { detailQuery }
}
