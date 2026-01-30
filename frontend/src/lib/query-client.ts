import { QueryClient } from '@tanstack/vue-query'

export const QUERY_CACHE_POLICY = {
  search: { staleTime: 1000 * 60 * 30, gcTime: 1000 * 60 * 60 },
  detail: { staleTime: 1000 * 60 * 60, gcTime: 1000 * 60 * 120 },
  stats: { staleTime: 1000 * 60 * 10, gcTime: 1000 * 60 * 30 },
} as const

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: QUERY_CACHE_POLICY.search.staleTime,
      gcTime: QUERY_CACHE_POLICY.search.gcTime,
      retry: 2,
      refetchOnWindowFocus: false,
      refetchOnMount: false,
    },
  },
})

