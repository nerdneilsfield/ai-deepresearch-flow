import 'fake-indexeddb/auto'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { state } = vi.hoisted(() => ({ state: { items: [] as unknown[], total: 0 } }))

vi.mock('@/components/RenderedMarkdown.vue', () => ({ default: { template: '<div />' } }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string, params?: Record<string, string>) => (params ? `${key} ${Object.values(params).join(' ')}` : key) }),
}))
vi.mock('@/stores/runtime-config', () => ({ useRuntimeConfigStore: () => ({ staticBaseUrl: '' }) }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast: vi.fn() }) }))
vi.mock('@/stores/selection', () => ({
  useSelectionStore: () => ({ selectedIds: new Set<string>(), isFull: false, toggle: vi.fn() }),
}))
vi.mock('@/stores/favorites', () => ({
  useFavoriteStore: () => ({
    favoriteIds: new Set<string>(), ratingsById: {}, ratingFor: () => undefined, toggle: vi.fn(), setRating: vi.fn(),
  }),
}))
vi.mock('@/lib/lazy', () => ({ lazySnippet: async () => (value: string) => value }))
vi.mock('@/composables/useExpandableSummary', async () => {
  const { ref } = await import('vue')
  return {
    useExpandableSummary: () => ({
      expanded: ref({}), expandedMarkdown: ref({}), expandedLoading: ref({}), toggleSummary: vi.fn(),
    }),
  }
})
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}))
vi.mock('@/composables/useSearch', async () => {
  const { computed, ref } = await import('vue')
  const query = ref('')
  const mode = ref<'keyword' | 'semantic'>('keyword')
  const facet = ref('')
  const facetId = ref('')
  const page = ref(1)
  const searchState = {
    query, page, pageSize: ref('20'), pageSizeNum: computed(() => 20), facet, facetId,
    facetByValue: ref(false), facetType: ref('authors'), facetPage: ref(1), facetPageSize: ref(20),
    facetSearch: ref(''), sortBy: ref('year_desc'), mode, effectiveSort: computed(() => 'year_desc'),
    syncFromRoute: () => undefined, syncToRoute: () => undefined, setFacet: () => undefined,
    clearFacet: () => { facet.value = ''; facetId.value = '' }, handleSearchInput: () => undefined,
  }
  return {
    useSearchState: () => searchState,
    useSearchData: () => ({
      searchQuery: {
        data: computed(() => ({ items: state.items, total: state.total, page: 1, page_size: 20 })),
        isFetching: ref(false), error: ref(null), refetch: () => undefined,
      },
      statsQuery: {
        data: ref({
          total: 3,
          authors: [{ value: 'Geoffrey Hinton', paper_count: 2 }],
          years: [{ value: '2019', paper_count: 1 }],
        }),
        error: ref(null),
      },
      facetQuery: { data: ref(null), error: ref(null) },
    }),
    __searchState: searchState,
  }
})

async function mountView() {
  const { default: SearchView } = await import('@/views/SearchView.vue')
  return mount(SearchView, { global: { stubs: { RecycleScroller: true, 'router-link': true } } })
}

async function type(wrapper: Awaited<ReturnType<typeof mountView>>, value: string) {
  await wrapper.find('[data-testid="search-input"]').setValue(value)
  await flushPromises()
}

beforeEach(async () => {
  state.items = []
  state.total = 0
  const { __searchState } = (await import('@/composables/useSearch')) as unknown as {
    __searchState: { query: { value: string }; mode: { value: string }; facet: { value: string }; facetId: { value: string } }
  }
  __searchState.query.value = ''
  __searchState.mode.value = 'keyword'
  __searchState.facet.value = ''
  __searchState.facetId.value = ''
})

describe('SearchView unified search box', () => {
  it('renders recognised conditions as chips and drops one when its × is clicked', async () => {
    const wrapper = await mountView()
    await type(wrapper, 'transformer author:"vaswani" year:2017.. -bert')

    const chips = wrapper.findAll('[data-testid="search-chip"]')
    expect(chips.map((chip) => chip.text())).toEqual([
      expect.stringContaining('vaswani'),
      expect.stringContaining('2017..'),
      expect.stringContaining('bert'),
    ])

    await chips[1]!.find('[data-testid="search-chip-remove"]').trigger('click')
    expect((wrapper.find('[data-testid="search-input"]').element as HTMLInputElement).value).toBe(
      'transformer author:"vaswani" -bert',
    )
    expect(wrapper.findAll('[data-testid="search-chip"]')).toHaveLength(2)
  })

  it('shows no chips for plain text and treats unknown fields as text', async () => {
    const wrapper = await mountView()
    await type(wrapper, 'foo:bar graph')
    expect(wrapper.find('[data-testid="search-chips"]').exists()).toBe(false)
  })

  it('disables the semantic option while unauthenticated', async () => {
    const wrapper = await mountView()
    const semantic = wrapper.find('[data-testid="search-mode-semantic"]')
    expect((semantic.element as HTMLButtonElement).disabled).toBe(true)
    await semantic.trigger('click')
    expect(wrapper.find('[data-testid="search-mode-keyword"]').attributes('aria-checked')).toBe('true')
  })

  it('advanced form writes the generated expression into the input', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-testid="search-advanced-toggle"]').trigger('click')
    await wrapper.find('[data-testid="adv-allWords"]').setValue('deep learning')
    await wrapper.find('[data-testid="adv-author"]').setValue('Hinton')
    await wrapper.find('[data-testid="adv-yearFrom"]').setValue('2015')
    await wrapper.find('[data-testid="search-advanced-form"]').trigger('submit')
    await flushPromises()

    expect((wrapper.find('[data-testid="search-input"]').element as HTMLInputElement).value).toBe(
      'deep learning author:"Hinton" year:2015..',
    )
    expect(wrapper.find('[data-testid="search-advanced-form"]').exists()).toBe(false)
  })

  it('sidebar author and year clicks append conditions instead of replacing the query', async () => {
    const wrapper = await mountView()
    await type(wrapper, 'cnn')
    await wrapper.find('[aria-label="Filter by author Geoffrey Hinton"]').trigger('click')
    await wrapper.find('[aria-label="Filter by year 2019"]').trigger('click')
    expect((wrapper.find('[data-testid="search-input"]').element as HTMLInputElement).value).toBe(
      'cnn author:"Geoffrey Hinton" year:2019',
    )
  })

  it('empty results name the conditions that can be removed', async () => {
    const wrapper = await mountView()
    await type(wrapper, 'zzz author:"nobody"')
    const empty = wrapper.find('[data-testid="search-empty"]')
    expect(empty.exists()).toBe(true)
    expect(empty.text()).toContain('author:"nobody"')
  })

  it('shows the active facet filter as a readable chip that can be cleared', async () => {
    const { __searchState } = (await import('@/composables/useSearch')) as unknown as {
      __searchState: { facet: { value: string }; facetId: { value: string } }
    }
    __searchState.facet.value = 'authors'
    __searchState.facetId.value = 'Hinton'
    const wrapper = await mountView()
    const filter = wrapper.find('[data-testid="facet-filter"]')
    expect(filter.text()).toContain('Hinton')
    expect(filter.text()).not.toContain('authors:')
    await wrapper.find('[data-testid="facet-chip-remove"]').trigger('click')
    expect(wrapper.find('[data-testid="facet-filter"]').exists()).toBe(false)
  })
})
