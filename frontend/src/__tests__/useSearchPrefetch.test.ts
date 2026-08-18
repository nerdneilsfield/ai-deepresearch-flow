import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { useSearchData, useSearchState } from '@/composables/useSearch'

const {
  getFacetMock,
  getFacetByValuePapersMock,
  getFacetPapersMock,
  getStatsMock,
  listPapersMock,
  searchPapersMock,
} = vi.hoisted(() => ({
  getFacetMock: vi.fn(),
  getFacetByValuePapersMock: vi.fn(),
  getFacetPapersMock: vi.fn(),
  getStatsMock: vi.fn(),
  listPapersMock: vi.fn(),
  searchPapersMock: vi.fn(),
}))

const route = ref({ query: {}, name: 'search', params: {} })

vi.mock('vue-router', () => ({
  useRoute: () => route.value,
  useRouter: () => ({ replace: vi.fn() }),
}))

vi.mock('@/lib/api', () => ({
  getFacet: getFacetMock,
  getFacetByValuePapers: getFacetByValuePapersMock,
  getFacetPapers: getFacetPapersMock,
  getStats: getStatsMock,
  listPapers: listPapersMock,
  searchPapers: searchPapersMock,
}))

function pageResponse(page: number, pageSize: number) {
  return {
    page,
    page_size: pageSize,
    total: 80,
    has_more: page < 4,
    items: [],
  }
}

describe('useSearchData page prefetch', () => {
  let client: QueryClient | null = null
  let state: ReturnType<typeof useSearchState> | null = null
  let currentPage: (() => number | undefined) | null = null

  afterEach(() => {
    client?.clear()
    client = null
    state = null
    currentPage = null
    vi.clearAllMocks()
  })

  it('warms adjacent query pages and reuses them when navigating back', async () => {
    searchPapersMock.mockImplementation(async (_query: string, page: number, pageSize: number) =>
      pageResponse(page, pageSize),
    )
    getStatsMock.mockResolvedValue({})
    getFacetMock.mockResolvedValue({ page: 1, page_size: 50, total: 0, has_more: false, items: [] })

    client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const TestComponent = defineComponent({
      setup() {
        state = useSearchState()
        state.query.value = 'retrieval'
        state.page.value = 2
        const { searchQuery } = useSearchData(state)
        currentPage = () => searchQuery.data.value?.page
        return {}
      },
      template: '<div />',
    })

    mount(TestComponent, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: client }]],
      },
    })

    await vi.waitFor(() => {
      const requestedPages = searchPapersMock.mock.calls.map(([, page]) => page)
      expect(requestedPages).toEqual(expect.arrayContaining([1, 2, 3]))
    })

    state!.page.value = 3
    await vi.waitFor(() => {
      expect(currentPage?.()).toBe(3)
      expect(searchPapersMock.mock.calls.filter(([, page]) => page === 3)).toHaveLength(1)
    })

    state!.page.value = 2
    await vi.waitFor(() => {
      expect(currentPage?.()).toBe(2)
      expect(searchPapersMock.mock.calls.filter(([, page]) => page === 2)).toHaveLength(1)
    })
  })
})
