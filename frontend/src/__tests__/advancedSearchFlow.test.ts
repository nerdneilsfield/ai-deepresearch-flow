import 'fake-indexeddb/auto'
import { flushPromises, shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { clearToken, getToken, setToken } from '@/lib/token-db'

const originalFetch = globalThis.fetch

const {
  fetchMock,
  pushToastMock,
} = vi.hoisted(() => ({
  fetchMock: vi.fn(),
  pushToastMock: vi.fn(),
}))

vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({
    pushToast: pushToastMock,
  }),
}))

vi.mock('@/stores/selection', () => ({
  useSelectionStore: () => ({
    selectedIds: new Set<string>(),
    isFull: false,
    toggle: vi.fn(),
  }),
}))

vi.mock('@/components/search/SearchResultItem.vue', () => ({
  default: {
    name: 'SearchResultItem',
    template: '<div data-testid="basic-result-stub" />',
  },
}))

vi.mock('@/lib/lazy', () => ({
  lazySnippet: async () => (value: string) => value,
}))

vi.mock('@/composables/useExpandableSummary', async () => {
  const { ref } = await import('vue')
  return {
    useExpandableSummary: () => ({
      expanded: ref<Record<string, boolean>>({}),
      expandedMarkdown: ref<Record<string, string>>({}),
      expandedLoading: ref<Record<string, boolean>>({}),
      toggleSummary: vi.fn(),
    }),
  }
})

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('@/composables/useSearch', async () => {
  const { computed, ref } = await import('vue')
  const query = ref('')
  const page = ref(1)
  const pageSize = ref('20')
  const pageSizeNum = computed(() => 20)
  const facet = ref('')
  const facetId = ref('')
  const facetByValue = ref(false)
  const facetType = ref('authors')
  const facetPage = ref(1)
  const facetPageSize = ref(20)
  const facetSearch = ref('')
  const sortBy = ref('year_desc')
  const searchQuery = {
    data: ref({
      items: [{ paper_id: 'basic-1', paper_index: 1 }],
      total: 1,
      page_size: 20,
    }),
    isFetching: ref(false),
    error: ref(null),
    refetch: vi.fn(),
  }
  const statsQuery = { data: ref(null), error: ref(null) }
  const facetQuery = { data: ref(null), error: ref(null) }

  return {
    useSearchState: () => ({
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
      effectiveSort: computed(() => sortBy.value),
      syncFromRoute: vi.fn(),
      syncToRoute: vi.fn(),
      setFacet: vi.fn(),
      clearFacet: vi.fn(),
      handleSearchInput: vi.fn(),
    }),
    useSearchData: () => ({
      searchQuery,
      statsQuery,
      facetQuery,
    }),
  }
})

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function mockAdvancedFetch(
  verifyResponses: Array<{ status: number; body: unknown }>,
  searchResponses: Array<{ status: number; body: unknown }>
) {
  const verifyQueue = [...verifyResponses]
  const searchQueue = [...searchResponses]
  fetchMock.mockImplementation(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString()
    if (url.includes('/search/advanced/verify-token')) {
      const next = verifyQueue.shift()
      if (!next) throw new Error(`unexpected verify fetch: ${url}`)
      return jsonResponse(next.status, next.body)
    }
    if (url.includes('/search/advanced?')) {
      const next = searchQueue.shift()
      if (!next) throw new Error(`unexpected search fetch: ${url}`)
      return jsonResponse(next.status, next.body)
    }
    throw new Error(`unexpected fetch: ${url}`)
  })
}

async function settle(wrapper: ReturnType<typeof shallowMount>) {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()
}

async function mountView() {
  const { default: SearchView } = await import('@/views/SearchView.vue')
  return shallowMount(SearchView, {
    global: {
      stubs: {
        AdvancedSearchPanel: false,
        AdvancedSearchResults: false,
        SearchResultItem: false,
      },
    },
  })
}

beforeEach(async () => {
  await clearToken()
  vi.restoreAllMocks()
  fetchMock.mockReset()
  pushToastMock.mockReset()
  const localStorageState = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => localStorageState.get(key) ?? null,
    setItem: (key: string, value: string) => {
      localStorageState.set(key, value)
    },
    removeItem: (key: string) => {
      localStorageState.delete(key)
    },
    clear: () => {
      localStorageState.clear()
    },
  })
  globalThis.fetch = fetchMock as unknown as typeof fetch
  const { useAdvancedSearchToken } = await import('@/composables/useAdvancedSearchToken')
  await useAdvancedSearchToken().clear()
})

afterEach(async () => {
  await clearToken()
  globalThis.fetch = originalFetch
})

describe('SearchView advanced search integration', () => {
  it('new user verifies a token and sees advanced results rendered through SearchView', async () => {
    mockAdvancedFetch(
      [{ status: 200, body: { valid: true } }],
      [{
        status: 200,
        body: {
          success: true,
          trace_id: 't-1',
          query: { raw: 'vision', normalized: 'vision', applied_filters: {} },
          results: [{
            chunk_id: 'p1_c0',
            paper_id: 'p1',
            paper: {
              title: 'Advanced Paper',
              authors: ['Alice'],
              year: '2024',
              venue: 'ICLR',
              doi: '',
              source_hash: 'h',
            },
            chunk: {
              text: 'advanced chunk body',
              field_name: 'simple/content',
              template_tag: 'simple',
              chunk_type: 'content',
              chunk_index: 0,
              lang: 'en',
            },
            scores: { fused: 0.1, final: 0.1 },
          }],
          metadata: {
            counts: {},
            fusion: 'rrf',
            reranker: { applied: false, model: null },
            mmr: { applied: true, lambda: 0.6 },
            embedding: { model: 'bge-m3', dimensions: 1024 },
            latency_ms: {},
          },
          degraded: false,
          degradation: null,
        },
      }],
    )

    const wrapper = await mountView()
    await settle(wrapper)
    expect(wrapper.findAll('[data-testid="basic-result-stub"]')).toHaveLength(1)

    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-query-input"]').setValue('vision')
    await wrapper.find('[data-testid="advanced-search-button"]').trigger('click')
    await settle(wrapper)

    expect(wrapper.find('[data-testid="advanced-result-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Advanced Paper')
    expect(wrapper.text()).toContain('advanced chunk body')
    expect(await getToken()).toBe('secret')
    expect(wrapper.findAll('[data-testid="basic-result-stub"]')).toHaveLength(1)
  })

  it('returning user auto-verifies stored token on mount and pre-fills the input', async () => {
    await setToken('saved-token')
    mockAdvancedFetch([{ status: 200, body: { valid: true } }], [])

    const wrapper = await mountView()
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')

    expect(wrapper.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(true)
    expect((wrapper.find('[data-testid="advanced-token-input"]').element as HTMLInputElement).value).toBe('saved-token')
  })

  it('token revoked mid-session clears auth state after a 401 advanced-search response', async () => {
    await setToken('saved-token')
    mockAdvancedFetch(
      [{ status: 200, body: { valid: true } }],
      [{
        status: 401,
        body: {
          success: false,
          trace_id: 't-401',
          error: {
            code: 'UNAUTHORIZED',
            message: 'invalid',
            details: { reason: 'invalid' },
          },
        },
      }],
    )

    const wrapper = await mountView()
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-query-input"]').setValue('vision')
    await wrapper.find('[data-testid="advanced-search-button"]').trigger('click')
    await settle(wrapper)

    expect(pushToastMock).toHaveBeenCalledWith(
      'Advanced search token is invalid. Please re-verify.',
      'error',
    )
    expect(wrapper.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(false)
    expect((wrapper.find('[data-testid="advanced-search-button"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(await getToken()).toBeNull()
  })

  it('invalid token stays unverified and does not persist to IndexedDB', async () => {
    mockAdvancedFetch([{ status: 401, body: { valid: false, reason: 'invalid' } }], [])

    const wrapper = await mountView()
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('bad-token')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await settle(wrapper)

    expect(wrapper.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(true)
    expect((wrapper.find('[data-testid="advanced-search-button"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(await getToken()).toBeNull()
  })

  it('basic search results remain rendered when the advanced panel is collapsed', async () => {
    const wrapper = await mountView()
    await settle(wrapper)

    expect(wrapper.findAll('[data-testid="basic-result-stub"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="advanced-panel-body"]').exists()).toBe(false)
  })

  it('degraded advanced-search response surfaces the backend message as a warning toast', async () => {
    mockAdvancedFetch(
      [{ status: 200, body: { valid: true } }],
      [{
        status: 200,
        body: {
          success: true,
          trace_id: 't-degraded',
          query: { raw: 'vision', normalized: 'vision', applied_filters: {} },
          results: [{
            chunk_id: 'p1_c0',
            paper_id: 'p1',
            paper: {
              title: 'Advanced Paper',
              authors: ['Alice'],
              year: '2024',
              venue: 'ICLR',
              doi: '',
              source_hash: 'h',
            },
            chunk: {
              text: 'advanced chunk body',
              field_name: 'simple/content',
              template_tag: 'simple',
              chunk_type: 'content',
              chunk_index: 0,
              lang: 'en',
            },
            scores: { fused: 0.1, final: 0.1 },
          }],
          metadata: {
            counts: {},
            fusion: 'rrf',
            reranker: { applied: false, model: 'Qwen/Qwen3-Reranker-4B' },
            mmr: { applied: true, lambda: 0.6 },
            embedding: { model: 'bge-m3', dimensions: 1024 },
            latency_ms: {},
          },
          degraded: true,
          degradation: {
            reason: 'reranker_failed',
            message: 'Reranking failed; results fall back to fused ranking.',
          },
        },
      }],
    )

    const wrapper = await mountView()
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-query-input"]').setValue('vision')
    await wrapper.find('[data-testid="advanced-search-button"]').trigger('click')
    await settle(wrapper)

    expect(pushToastMock).toHaveBeenCalledWith(
      'Reranking failed; results fall back to fused ranking.',
      'warning',
    )
  })
})
