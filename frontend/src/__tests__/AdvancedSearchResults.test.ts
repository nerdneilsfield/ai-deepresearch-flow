import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import AdvancedSearchResults from '@/components/AdvancedSearchResults.vue'
import type { AdvancedSearchResult } from '@/lib/advanced-search'

function sample(): AdvancedSearchResult {
  return {
    chunk_id: 'p1_c0',
    paper_id: 'p1',
    paper: {
      title: 'An Image is Worth 16x16 Words',
      authors: ['Dosovitskiy A.'],
      year: '2020',
      venue: 'ICLR',
      doi: '10.x',
      source_hash: 'h',
    },
    chunk: {
      text: 'body...',
      field_name: 'simple/content',
      template_tag: 'simple',
      chunk_type: 'content',
      chunk_index: 0,
      lang: 'en',
    },
    scores: { dense: 0.84, sparse: 12.37, fused: 0.016, reranker: 0.912, final: 0.912 },
  }
}

describe('AdvancedSearchResults', () => {
  it('renders one card per result', () => {
    const wrapper = mount(AdvancedSearchResults, { props: { results: [sample(), sample()] } })
    expect(wrapper.findAll('[data-testid="advanced-result-card"]')).toHaveLength(2)
  })

  it('renders paper title and authors', () => {
    const wrapper = mount(AdvancedSearchResults, { props: { results: [sample()] } })
    expect(wrapper.text()).toContain('An Image is Worth 16x16 Words')
    expect(wrapper.text()).toContain('Dosovitskiy A.')
  })

  it('renders degradation banner when degraded', () => {
    const wrapper = mount(AdvancedSearchResults, {
      props: { results: [], degraded: true, degradationReason: 'reranker_failed' },
    })
    expect(wrapper.find('[data-testid="advanced-degraded-banner"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('reranker_failed')
  })

  it('hides degradation banner when not degraded', () => {
    const wrapper = mount(AdvancedSearchResults, { props: { results: [sample()] } })
    expect(wrapper.find('[data-testid="advanced-degraded-banner"]').exists()).toBe(false)
  })

  it('renders empty-state when no results', () => {
    const wrapper = mount(AdvancedSearchResults, { props: { results: [] } })
    expect(wrapper.find('[data-testid="advanced-results-empty"]').exists()).toBe(true)
  })
})
