import { describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { useFacetStats } from '@/composables/useFacetStats'

vi.mock('@/lib/api', () => ({
  getFacetByValueStats: vi.fn(async () => ({
    facet_type: 'authors',
    value: 'Alice',
    total: 1,
    related: {},
  })),
  getFacetByValuePapers: vi.fn(async () => ({
    page: 1,
    page_size: 20,
    total: 1,
    has_more: false,
    items: [],
  })),
}))

describe('useFacetStats', () => {
  it('fetches stats and papers with facet/value', async () => {
    const client = new QueryClient()
    const facet = ref('authors')
    const value = ref('Alice')
    const page = ref(1)
    const pageSize = ref(20)
    const TestComp = defineComponent({
      setup() {
        return useFacetStats(facet, value, page, pageSize)
      },
      template: '<div />',
    })

    mount(TestComp, {
      global: {
        plugins: [[VueQueryPlugin, { queryClient: client }]],
      },
    })

    await client.invalidateQueries()
    await new Promise((resolve) => setTimeout(resolve, 0))

    const api = await import('@/lib/api')
    expect(api.getFacetByValueStats).toHaveBeenCalledWith('authors', 'Alice')
    expect(api.getFacetByValuePapers).toHaveBeenCalledWith('authors', 'Alice', 1, 20)
  })
})
