import { reactive, ref, nextTick } from 'vue'
import { shallowMount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const route = reactive({ name: 'search' })
const admin = reactive({ authenticated: false, token: null as string | null, restore: vi.fn() })
const selection = reactive({ count: 0, init: vi.fn() })
const favorites = reactive({ count: 0, init: vi.fn() })
const routerPush = vi.fn()

vi.mock('vue-router', () => ({
  useRoute: () => route,
  useRouter: () => ({ push: routerPush }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: ref('en'),
    t: (key: string) => key,
  }),
}))

vi.mock('@/i18n', () => ({ setLocale: vi.fn() }))
vi.mock('@/stores/selection', () => ({ useSelectionStore: () => selection }))
vi.mock('@/stores/favorites', () => ({ useFavoriteStore: () => favorites }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ detailTitle: '', detailSubtitle: '' }) }))
vi.mock('@/stores/admin-pipeline', () => ({ useAdminPipelineStore: () => admin }))
vi.mock('@/composables/useTheme', () => ({
  useTheme: () => ({ themeMode: ref('light'), setTheme: vi.fn() }),
}))
vi.mock('@vueuse/core', () => ({
  useOnline: () => ref(true),
  useWindowScroll: () => ({ y: ref(0) }),
}))

describe('admin pipeline navigation', () => {
  it('hides disabled pipeline navigation until authentication is enabled', async () => {
    const { default: App } = await import('@/App.vue')
    const wrapper = shallowMount(App)

    expect(wrapper.text()).not.toContain('Admin')

    admin.authenticated = true
    await nextTick()
    expect(wrapper.text()).toContain('Admin')

    admin.authenticated = false
    await nextTick()
    expect(wrapper.text()).not.toContain('Admin')
  })
})
