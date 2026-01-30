import { describe, expect, it, vi } from 'vitest'
import { defineComponent, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { VueQueryPlugin, QueryClient } from '@tanstack/vue-query'
import { usePaperDetail } from '@/composables/usePaperDetail'

const mockDetail = {
  paper_id: 'paper-1',
  title: 'Test Title',
  year: '2024',
  venue: 'Test Venue',
  authors: [],
  keywords: [],
  institutions: [],
  tags: [],
}

vi.mock('@/lib/api', () => ({
  getPaperDetail: vi.fn(async () => mockDetail),
}))

describe('usePaperDetail', () => {
  it('fetches detail data via query', async () => {
    const client = new QueryClient()
    const paperId = ref('paper-1')
    const TestComp = defineComponent({
      setup() {
        return usePaperDetail(paperId)
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

    const { getPaperDetail } = await import('@/lib/api')
    expect(getPaperDetail).toHaveBeenCalledWith('paper-1')
  })
})
