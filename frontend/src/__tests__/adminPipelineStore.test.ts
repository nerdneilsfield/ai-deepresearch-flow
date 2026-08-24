import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useAdminPipelineStore } from '@/stores/admin-pipeline'

function configPayload(defaultOcr: string) {
  return {
    enabled: true,
    models: {
      ocr: { allowlist: [defaultOcr], default: defaultOcr },
      extract: { allowlist: ['extract-a'], default: 'extract-a' },
      translate: { allowlist: ['translate-a'], default: 'translate-a' },
    },
    limits: { pdfs_per_batch: 2, max_pdf_bytes: 100, max_batch_bytes: 150, bibtex_max_bytes: 50 },
    worker: { status: 'online', active_jobs: 0 },
  }
}

describe('admin pipeline authentication state', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('keeps newest login state when validation responses arrive out of order', async () => {
    const responses: Array<(response: Response) => void> = []
    globalThis.fetch = vi.fn(() => new Promise<Response>((resolve) => {
      responses.push(resolve)
    })) as unknown as typeof fetch
    const store = useAdminPipelineStore()

    const firstLogin = store.login('first-secret')
    const secondLogin = store.login('second-secret')

    responses[1]?.(new Response(JSON.stringify(configPayload('ocr-second')), { status: 200 }))
    await expect(secondLogin).resolves.toBe(true)
    responses[0]?.(new Response(JSON.stringify(configPayload('ocr-first')), { status: 200 }))
    await expect(firstLogin).resolves.toBe(false)

    expect(store.token).toBe('second-secret')
    expect(store.config?.models.ocr.default).toBe('ocr-second')
    expect(sessionStorage.getItem('paper-db-admin-pipeline-token')).toBe('second-secret')
    expect(store.authenticated).toBe(true)
  })
})
