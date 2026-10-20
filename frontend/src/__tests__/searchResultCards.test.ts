import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/components/RenderedMarkdown.vue', () => ({ default: { template: '<div />' } }))
vi.mock('@/stores/runtime-config', () => ({ useRuntimeConfigStore: () => ({ staticBaseUrl: '' }) }))
vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const item = {
  paper_id: 'p1',
  title: 'Attention Is All You Need',
  year: '2017',
  venue: 'NeurIPS',
  authors: ['Vaswani A.'],
  summary_preview: 'A summary',
  has_pdf: true,
  has_source: false,
  has_translated: false,
}

async function mountItem(props: Record<string, unknown> = {}) {
  const { default: SearchResultItem } = await import('@/components/search/SearchResultItem.vue')
  return mount(SearchResultItem, {
    props: { item, displayIndex: 3, isSelected: false, selectionFull: false, snippetRenderer: (v: string) => v, ...props },
    global: { stubs: { 'router-link': { template: '<a><slot /></a>' } } },
  })
}

describe('SearchResultItem', () => {
  it('shows authors, venue and year on one meta line', async () => {
    const wrapper = await mountItem()
    const meta = wrapper.find('[data-testid="result-meta"]').text()
    expect(meta).toContain('Vaswani A.')
    expect(meta).toContain('NeurIPS · 2017')
  })

  it('has labelled icon buttons that emit select and favorite', async () => {
    const wrapper = await mountItem()
    const select = wrapper.find('[data-testid="result-select"]')
    expect(select.attributes('aria-label')).toBe('select')
    await select.trigger('click')
    await wrapper.find('[data-testid="favorite-toggle"]').trigger('click')
    expect(wrapper.emitted('toggleSelect')).toHaveLength(1)
    expect(wrapper.emitted('toggleFavorite')).toHaveLength(1)
  })

  it('reflects the selected state and keeps the resource badge and index in the footer', async () => {
    const wrapper = await mountItem({ isSelected: true })
    expect(wrapper.find('[data-testid="result-select"]').attributes('aria-label')).toBe('selected_btn')
    expect(wrapper.find('[data-testid="result-select"]').attributes('aria-pressed')).toBe('true')
    expect(wrapper.text()).toContain('PDF')
    expect(wrapper.text()).toContain('#3')
  })
})

describe('AdvancedSearchResults semantic cards', () => {
  const result = {
    chunk_id: 'c1',
    paper_id: 'p1',
    paper: { title: 'ViT', authors: ['Dosovitskiy A.'], year: '2020', venue: 'ICLR', doi: '', source_hash: '' },
    chunk: { text: 'Patches of an image are tokens', field_name: 'simple/content', template_tag: 'simple', chunk_type: 'content', chunk_index: 0, lang: 'en' },
    scores: { fused: 0.1, final: 0.8123 },
  }

  it('shows chunk source and a weakened score, and highlights query terms', async () => {
    const { default: AdvancedSearchResults } = await import('@/components/AdvancedSearchResults.vue')
    const wrapper = mount(AdvancedSearchResults, { props: { results: [result], highlightTerms: ['image'] } })
    expect(wrapper.text()).toContain('simple/content')
    expect(wrapper.find('[data-testid="advanced-result-score"]').text()).toContain('scoreLabel')
    expect(wrapper.find('mark').text()).toBe('image')
  })

  it('empty state lists the removable conditions', async () => {
    const { default: AdvancedSearchResults } = await import('@/components/AdvancedSearchResults.vue')
    const wrapper = mount(AdvancedSearchResults, { props: { results: [], removableHints: ['year:2030'] } })
    expect(wrapper.find('[data-testid="advanced-results-empty"]').text()).toContain('year:2030')
  })
})
