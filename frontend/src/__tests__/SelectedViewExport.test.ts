import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SearchItem } from '@/types/api'

const { selectionState, pushToastMock, discoverSummaryTemplatesMock, downloadSelectedJsonlMock, downloadSelectedZipMock, selectedExportIssueCountMock } = vi.hoisted(() => ({
  selectionState: {
    items: [] as SearchItem[],
    get count() { return this.items.length },
    isFull: false,
    clear: vi.fn(),
    toggle: vi.fn(),
  },
  pushToastMock: vi.fn(),
  discoverSummaryTemplatesMock: vi.fn(),
  downloadSelectedJsonlMock: vi.fn(),
  downloadSelectedZipMock: vi.fn(),
  selectedExportIssueCountMock: vi.fn(),
}))

vi.mock('@/stores/selection', () => ({
  useSelectionStore: () => selectionState,
}))

vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({ pushToast: pushToastMock }),
}))

vi.mock('@/lib/selected-export', async () => ({
  discoverSummaryTemplates: discoverSummaryTemplatesMock,
  downloadSelectedJsonl: downloadSelectedJsonlMock,
  downloadSelectedZip: downloadSelectedZipMock,
  selectedExportIssueCount: selectedExportIssueCountMock,
}))

vi.mock('@/lib/lazy', () => ({
  lazySnippet: async () => (value: string) => value,
  lazySaveAs: async () => vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  getPaperDetail: vi.fn(),
  matchBibtex: vi.fn(),
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

vi.mock('@/components/search/SearchResultItem.vue', () => ({
  default: { name: 'SearchResultItem', template: '<div data-testid="selected-result" />' },
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => params?.count ? `${key} ${params.count}` : key,
  }),
}))

async function settle(wrapper: ReturnType<typeof shallowMount>) {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()
}

function makeItem(overrides: Partial<SearchItem> = {}): SearchItem {
  return {
    paper_id: 'paper-1',
    title: 'Paper',
    year: '2026',
    venue: 'Venue',
    authors: ['Ada'],
    summary_url: 'https://example.test/summary.json',
    preferred_summary_template: 'default',
    ...overrides,
  }
}

async function mountView() {
  const { default: SelectedView } = await import('@/views/SelectedView.vue')
  return shallowMount(SelectedView, {
    global: {
      stubs: {
        Button: { template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
        Progress: true,
        SearchResultItem: false,
      },
    },
  })
}

beforeEach(() => {
  selectionState.items = [makeItem()]
  selectionState.clear.mockReset()
  selectionState.toggle.mockReset()
  pushToastMock.mockReset()
  discoverSummaryTemplatesMock.mockReset()
  discoverSummaryTemplatesMock.mockResolvedValue({ templates: ['default', 'deep_read'], preferredTemplates: ['default'] })
  downloadSelectedJsonlMock.mockReset()
  downloadSelectedJsonlMock.mockResolvedValue({
    saved: true,
    stats: { missingAssets: 0, failedAssets: 0, missingSummaries: 0, failedSummaries: 0, metadataFailures: 0 },
  })
  selectedExportIssueCountMock.mockReset()
  selectedExportIssueCountMock.mockReturnValue(0)
  downloadSelectedZipMock.mockReset()
  downloadSelectedZipMock.mockResolvedValue({
    saved: true,
    stats: { missingAssets: 0, failedAssets: 0, missingSummaries: 0, failedSummaries: 0, metadataFailures: 0 },
  })
})

describe('SelectedView export options', () => {
  it('shows ZIP options by default and switches JSONL to structured-only options', async () => {
    const wrapper = await mountView()
    await settle(wrapper)

    expect(wrapper.text()).toContain('selectedExportZip')
    expect(wrapper.text()).toContain('selectedExportPdf')
    expect(wrapper.text()).toContain('selectedExportImages')

    const jsonlButton = wrapper.findAll('button').find((button) => button.text().includes('selectedExportJsonl'))
    expect(jsonlButton).toBeTruthy()
    await jsonlButton!.trigger('click')
    await settle(wrapper)

    expect(wrapper.text()).toContain('selectedExportDownloadJsonl')
    expect(wrapper.text()).not.toContain('selectedExportPdf')
    expect(wrapper.text()).not.toContain('selectedExportImages')
  })

  it('passes selected JSONL options to the export helper', async () => {
    const wrapper = await mountView()
    await settle(wrapper)
    const jsonlButton = wrapper.findAll('button').find((button) => button.text().includes('selectedExportJsonl'))
    await jsonlButton!.trigger('click')
    await settle(wrapper)

    const downloadButton = wrapper.findAll('button').find((button) => button.text().includes('selectedExportDownloadJsonl'))
    await downloadButton!.trigger('click')
    await settle(wrapper)

    expect(downloadSelectedJsonlMock).toHaveBeenCalledWith(
      [expect.objectContaining({ paper_id: 'paper-1' })],
      expect.objectContaining({ mode: 'jsonl', includeMetadata: false, includePdf: false, summaryTemplates: ['default'] }),
      expect.any(Object),
    )
    expect(pushToastMock).toHaveBeenCalledWith('selectedExportCompleted', 'success')
  })


  it('disables JSONL download when no JSONL content is selected', async () => {
    const wrapper = await mountView()
    await settle(wrapper)
    const jsonlButton = wrapper.findAll('button').find((button) => button.text().includes('selectedExportJsonl'))
    await jsonlButton!.trigger('click')
    await settle(wrapper)

    const summaryLabel = wrapper.findAll('label').find((label) => label.text().includes('selectedExportSummaryTemplates'))
    expect(summaryLabel).toBeTruthy()
    await summaryLabel!.find('input').setValue(false)
    await settle(wrapper)

    const downloadButton = wrapper.findAll('button').find((button) => button.text().includes('selectedExportDownloadJsonl'))
    expect(downloadButton?.attributes('disabled')).toBeDefined()

    const metadataLabel = wrapper.findAll('label').find((label) => label.text().includes('selectedExportMetadata'))
    expect(metadataLabel).toBeTruthy()
    await metadataLabel!.find('input').setValue(true)
    await settle(wrapper)

    expect(downloadButton?.attributes('disabled')).toBeUndefined()
  })

  it('reports partial success from export stats', async () => {
    selectedExportIssueCountMock.mockReturnValueOnce(3)
    downloadSelectedZipMock.mockResolvedValueOnce({
      saved: true,
      stats: { missingAssets: 1, failedAssets: 0, missingSummaries: 2, failedSummaries: 0, metadataFailures: 0 },
    })
    const wrapper = await mountView()
    await settle(wrapper)
    const downloadButton = wrapper.findAll('button').find((button) => button.text().includes('downloadZip'))
    await downloadButton!.trigger('click')
    await settle(wrapper)

    expect(pushToastMock).toHaveBeenCalledWith('selectedExportCompletedWithMissing 3', 'warning')
  })
})
