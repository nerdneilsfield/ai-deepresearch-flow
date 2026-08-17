import { ref } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FavoriteRecord } from '@/types/favorites'

const { favoriteState, selectionState, routerPush, pushToast } = vi.hoisted(() => ({
  favoriteState: {
    count: 0,
    items: [] as FavoriteRecord[],
    sortedItems: [] as FavoriteRecord[],
    init: vi.fn(),
    remove: vi.fn(),
    setRating: vi.fn(),
    merge: vi.fn(),
    replace: vi.fn(),
  },
  selectionState: {
    selectedIds: new Set<string>(),
    isFull: false,
    toggle: vi.fn(),
  },
  routerPush: vi.fn(),
  pushToast: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => `${key} ${params?.count ?? params?.rating ?? ''}`.trim(),
  }),
}))

vi.mock('@/stores/favorites', () => ({
  useFavoriteStore: () => favoriteState,
}))

vi.mock('@/stores/selection', () => ({
  useSelectionStore: () => selectionState,
}))

vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ pushToast }),
}))

vi.mock('@/lib/lazy', () => ({
  lazySnippet: async () => (value: string) => value,
  lazySaveAs: async () => vi.fn(),
}))

vi.mock('@/composables/useExpandableSummary', () => ({
  useExpandableSummary: () => ({
    expanded: ref<Record<string, boolean>>({}),
    expandedMarkdown: ref<Record<string, string>>({}),
    expandedLoading: ref<Record<string, boolean>>({}),
    toggleSummary: vi.fn(),
  }),
}))

vi.mock('@/components/search/SearchResultItem.vue', () => ({
  default: {
    name: 'SearchResultItem',
    props: ['item', 'favoriteRating'],
    emits: ['toggle-favorite', 'set-favorite-rating'],
    template: `
      <article data-testid="favorite-card">
        <span>{{ item.paper_id }}</span>
        <span>{{ favoriteRating }}</span>
        <button data-testid="remove-favorite" @click="$emit('toggle-favorite')">remove</button>
      </article>
    `,
  },
}))

function makeFavorite(paperId: string, rating: 1 | 2 | 3 | 4 | 5): FavoriteRecord {
  return {
    paper: {
      paper_id: paperId,
      title: `Paper ${paperId}`,
      year: '2026',
      venue: 'ICLR',
      authors: ['Ada'],
    },
    rating,
    createdAt: 1,
    updatedAt: 1,
  }
}

async function mountView() {
  const { default: FavoritesView } = await import('@/views/FavoritesView.vue')
  return mount(FavoritesView, {
    global: {
      stubs: {
        Button: { emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
      },
    },
  })
}

beforeEach(() => {
  favoriteState.count = 2
  favoriteState.items = [makeFavorite('five-star', 5), makeFavorite('four-star', 4)]
  favoriteState.sortedItems = favoriteState.items
  favoriteState.init.mockReset()
  favoriteState.init.mockResolvedValue(undefined)
  favoriteState.remove.mockReset()
  favoriteState.setRating.mockReset()
  favoriteState.merge.mockReset()
  favoriteState.replace.mockReset()
  selectionState.toggle.mockReset()
  routerPush.mockReset()
  pushToast.mockReset()
})

describe('FavoritesView', () => {
  it('shows sorted favorites, filters by rating, and removes through the favorite action', async () => {
    const wrapper = await mountView()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('[data-testid="favorite-card"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('five-star')

    await wrapper.find('[data-testid="favorites-rating-filter"]').setValue('4')
    expect(wrapper.findAll('[data-testid="favorite-card"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('four-star')

    await wrapper.find('[data-testid="remove-favorite"]').trigger('click')
    expect(favoriteState.remove).toHaveBeenCalledWith('four-star')
  })

  it('shows the empty state when there are no favorites', async () => {
    favoriteState.count = 0
    favoriteState.items = []
    favoriteState.sortedItems = []

    const wrapper = await mountView()
    await wrapper.vm.$nextTick()

    expect(wrapper.findAll('[data-testid="favorite-card"]')).toHaveLength(0)
    expect(wrapper.text()).toContain('noFavorites')
  })

  it('offers merge and replace modes before loading a list', async () => {
    const wrapper = await mountView()
    await wrapper.vm.$nextTick()

    const loadButton = wrapper.findAll('button').find((button) => button.text().includes('loadList'))
    expect(loadButton).toBeTruthy()
    await loadButton!.trigger('click')

    expect(wrapper.text()).toContain('listImportMerge')
    expect(wrapper.text()).toContain('listImportReplace')
  })
})
