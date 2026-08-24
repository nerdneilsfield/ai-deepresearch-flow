import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'

const routerPush = vi.fn()
const routerReplace = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPush, replace: routerReplace }),
  useRoute: () => ({ params: {} }),
}))

const originalFetch = globalThis.fetch

function configPayload() {
  return {
    enabled: true,
    models: {
      ocr: { allowlist: ['ocr-a', 'ocr-b'], default: 'ocr-a' },
      extract: { allowlist: ['extract-a'], default: 'extract-a' },
      translate: { allowlist: ['translate-a'], default: 'translate-a' },
    },
    limits: { pdfs_per_batch: 2, max_pdf_bytes: 100, max_batch_bytes: 150, bibtex_max_bytes: 50 },
    worker: { status: 'online', active_jobs: 0 },
  }
}

describe('admin pipeline upload view', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    globalThis.fetch = vi.fn() as unknown as typeof fetch
    routerPush.mockReset()
    routerReplace.mockReset()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('validates token before rendering upload controls and uses model defaults', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(new Response(JSON.stringify(configPayload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ page: 1, page_size: 20, total: 0, has_more: false, items: [] }), { status: 200 }))

    const { default: AdminPipelineView } = await import('@/views/AdminPipelineView.vue')
    const wrapper = mount(AdminPipelineView)
    await wrapper.get('[data-testid="admin-token-input"]').setValue('session-secret')
    await wrapper.get('form').trigger('submit')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(sessionStorage.getItem('paper-db-admin-pipeline-token')).toBe('session-secret')
    expect(wrapper.find('[data-testid="pdf-input"]').exists()).toBe(true)
    expect(wrapper.get('#ocr-model').element).toHaveProperty('value', 'ocr-a')
    expect(wrapper.get('#extract-model').element).toHaveProperty('value', 'extract-a')
  })

  it('shows aggregate validation before upload', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mockResolvedValueOnce(new Response(JSON.stringify(configPayload()), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ page: 1, page_size: 20, total: 0, has_more: false, items: [] }), { status: 200 }))

    const { default: AdminPipelineView } = await import('@/views/AdminPipelineView.vue')
    const wrapper = mount(AdminPipelineView)
    await wrapper.get('[data-testid="admin-token-input"]').setValue('session-secret')
    await wrapper.get('form').trigger('submit')
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()
    const first = new File([new Uint8Array(100)], 'first.pdf', { type: 'application/pdf' })
    const second = new File([new Uint8Array(100)], 'second.pdf', { type: 'application/pdf' })
    const input = wrapper.get('[data-testid="pdf-input"]')
    Object.defineProperty(input.element, 'files', { value: [first, second] })
    await input.trigger('change')

    expect(wrapper.get('[data-testid="upload-validation-error"]').text()).toContain('Combined PDF size exceeds batch limit.')
    expect(wrapper.get('[data-testid="upload-submit"]').attributes('disabled')).toBeDefined()
  })

  it('clears an invalid session token before exposing admin navigation state', async () => {
    sessionStorage.setItem('paper-db-admin-pipeline-token', 'expired-secret')
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ error: { code: 'unauthorized', message: 'authentication required' } }), { status: 401 }),
    )
    const { default: AdminPipelineView } = await import('@/views/AdminPipelineView.vue')
    const wrapper = mount(AdminPipelineView)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(sessionStorage.getItem('paper-db-admin-pipeline-token')).toBeNull()
    expect(wrapper.find('[data-testid="admin-token-input"]').exists()).toBe(true)
  })
})
