import { describe, expect, it, vi, beforeEach } from 'vitest'
import { useSearchState } from '@/composables/useSearch'
import { DEFAULT_PAGE_SIZE } from '@/lib/config'

let routeMock: { query: Record<string, string | undefined> }
const replaceMock = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => routeMock,
  useRouter: () => ({
    replace: replaceMock,
  }),
}))

describe('useSearchState', () => {
  beforeEach(() => {
    routeMock = { query: {} }
    replaceMock.mockClear()
  })

  it('syncs from route query', () => {
    routeMock.query = {
      q: 'slam',
      page: '2',
      page_size: '50',
      facet: 'authors',
      facet_id: 'alice',
      facet_by_value: '1',
      sort: 'year_desc',
    }
    const state = useSearchState()
    state.syncFromRoute()

    expect(state.query.value).toBe('slam')
    expect(state.page.value).toBe(2)
    expect(state.pageSize.value).toBe('50')
    expect(state.facet.value).toBe('authors')
    expect(state.facetId.value).toBe('alice')
    expect(state.facetByValue.value).toBe(true)
    expect(state.sortBy.value).toBe('year_desc')
  })

  it('syncs to route query with defaults omitted', () => {
    const state = useSearchState()
    state.query.value = ''
    state.page.value = 1
    state.pageSize.value = String(DEFAULT_PAGE_SIZE)
    state.sortBy.value = 'relevance'
    state.syncToRoute()

    expect(replaceMock).toHaveBeenCalledOnce()
    const args = replaceMock.mock.calls[0]?.[0]
    expect(args.query).toEqual({})
  })

  it('includes facet parameters when set', () => {
    const state = useSearchState()
    state.facet.value = 'authors'
    state.facetId.value = 'bob'
    state.facetByValue.value = true
    state.syncToRoute()

    const args = replaceMock.mock.calls[0]?.[0]
    expect(args.query).toMatchObject({
      facet: 'authors',
      facet_id: 'bob',
      facet_by_value: '1',
    })
  })
})
