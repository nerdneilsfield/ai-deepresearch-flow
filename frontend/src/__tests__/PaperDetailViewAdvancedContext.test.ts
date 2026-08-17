import { reactive, ref } from 'vue'
import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const routeState = reactive({
  params: { paperId: 'p1' },
  query: {} as Record<string, string>,
})

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({
    pushToast: vi.fn(),
    setDetailHeader: vi.fn(),
  }),
}))

vi.mock('@/stores/selection', () => ({
  useSelectionStore: () => ({
    selectedIds: new Set<string>(),
    getPrevId: vi.fn(() => null),
    getNextId: vi.fn(() => null),
  }),
}))

vi.mock('@/stores/favorites', () => ({
  useFavoriteStore: () => ({
    favoriteIds: new Set<string>(),
    ratingFor: () => undefined,
    toggle: vi.fn(),
    setRating: vi.fn(),
  }),
}))

vi.mock('@/stores/runtime-config', () => ({
  useRuntimeConfigStore: () => ({
    staticBaseUrl: '',
  }),
}))

vi.mock('@vueuse/core', () => ({
  useElementBounding: () => ({ top: ref(0) }),
  useMediaQuery: () => ref(true),
  useWindowSize: () => ({ height: ref(1080) }),
  refDebounced: (value: unknown) => value,
}))

vi.mock('@/composables/usePaperDetail', () => ({
  usePaperDetail: () => ({
    detailQuery: {
      data: ref({
        paper_id: 'p1',
        title: 'Advanced Paper',
        year: '2024',
        venue: 'ICLR',
        authors: ['Alice'],
        keywords: [],
        institutions: [],
        tags: [],
        summary_urls: {},
        translated_md_urls: {},
      }),
      isLoading: ref(false),
      error: ref(null),
    },
  }),
}))

vi.mock('@/composables/useSplitView', () => ({
  useSplitView: () => ({
    viewMode: ref('summary'),
    contentTab: ref('source'),
    leftView: ref('source'),
    rightView: ref('summary'),
    splitPercent: ref(50),
    detailWidthPercent: ref(80),
    swapSplit: vi.fn(),
    widenLeft: vi.fn(),
    tightenLeft: vi.fn(),
  }),
}))

vi.mock('@/lib/static-base', () => ({
  resolveStaticBaseUrl: () => '',
}))

vi.mock('@/components/SummaryPanel.vue', () => ({
  default: { name: 'SummaryPanel', template: '<div />' },
}))

vi.mock('@/components/MarkdownPanel.vue', () => ({
  default: { name: 'MarkdownPanel', template: '<div />' },
}))

vi.mock('@/components/PdfViewer.vue', () => ({
  default: { name: 'PdfViewer', template: '<div />' },
}))

vi.mock('@/components/paper-detail/MetadataPanel.vue', () => ({
  default: { name: 'MetadataPanel', template: '<div />' },
}))

describe('PaperDetailView advanced chunk context', () => {
  beforeEach(() => {
    routeState.params.paperId = 'p1'
    routeState.query = {}
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    })
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      media: '(prefers-color-scheme: dark)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })))
  })

  it('renders the matched chunk summary when advanced context is present in the route', async () => {
    routeState.query = {
      advanced_chunk_id: 'p1_c0',
      advanced_chunk_text: 'matched advanced chunk text',
      advanced_chunk_field: 'simple/content',
    }

    const { default: PaperDetailView } = await import('@/views/PaperDetailView.vue')
    const wrapper = shallowMount(PaperDetailView)

    expect(wrapper.find('[data-testid="advanced-match-banner"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('matched advanced chunk text')
    expect(wrapper.text()).toContain('simple/content')
  })
})
