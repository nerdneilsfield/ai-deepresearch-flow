import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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
  getPaperDetailCached: vi.fn(async () => mockDetail),
}))

async function wipe(): Promise<void> {
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase('deepresearch_paper_content_cache')
    req.onsuccess = req.onerror = req.onblocked = () => resolve()
  })
}

beforeEach(wipe)
afterEach(wipe)

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

    const { getPaperDetailCached } = await import('@/lib/api')
    expect(getPaperDetailCached).toHaveBeenCalledWith('paper-1', expect.any(Object))
  })
})
