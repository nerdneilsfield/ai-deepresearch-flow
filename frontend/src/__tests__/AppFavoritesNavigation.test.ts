import { reactive, ref } from 'vue'
import { shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const route = reactive({ name: 'search' })
const selection = reactive({ count: 0, init: vi.fn() })
const favorites = reactive({ count: 1, init: vi.fn() })
const routerPush = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: routerPush }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: ref('en'),
    t: (key: string, params?: Record<string, unknown>) => `${key} ${params?.count ?? ''}`.trim(),
  }),
}))

vi.mock('@/i18n', () => ({ setLocale: vi.fn() }))
vi.mock('@/stores/selection', () => ({ useSelectionStore: () => selection }))
vi.mock('@/stores/favorites', () => ({ useFavoriteStore: () => favorites }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ detailTitle: '', detailSubtitle: '' }) }))
vi.mock('@/composables/useTheme', () => ({ useTheme: () => ({ themeMode: ref('light'), setTheme: vi.fn() }) }))
vi.mock('@vueuse/core', () => ({ useOnline: () => ref(true), useWindowScroll: () => ({ y: ref(0) }) }))

describe('App favorite navigation', () => {
  it('renders and updates the local favorite count in desktop navigation', async () => {
    const { default: App } = await import('@/App.vue')
    const wrapper = shallowMount(App)

    expect(wrapper.text()).toContain('favorites 1')

    favorites.count = 2
    await wrapper.vm.$nextTick()
    expect(wrapper.text()).toContain('favorites 2')
  })

  it('links desktop navigation to explicit manual sync', async () => {
    const { default: App } = await import('@/App.vue')
    const wrapper = shallowMount(App)
    const syncButton = wrapper.findAll('button').find((button) => button.text().includes('sync'))

    expect(syncButton).toBeTruthy()
    await syncButton!.trigger('click')
    expect(routerPush).toHaveBeenCalledWith('/sync')
  })
})
