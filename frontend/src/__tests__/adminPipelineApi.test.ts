import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  ADMIN_TOKEN_STORAGE_KEY,
  createPipelineBatch,
  fetchAdminArtifact,
  getAdminToken,
  setAdminToken,
} from '@/lib/admin-pipeline'

describe('admin pipeline API', () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    sessionStorage.clear()
    globalThis.fetch = vi.fn() as unknown as typeof fetch
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
  })

  it('keeps admin token in sessionStorage only', () => {
    setAdminToken('secret')

    expect(getAdminToken()).toBe('secret')
    expect(sessionStorage.getItem(ADMIN_TOKEN_STORAGE_KEY)).toBe('secret')
    expect(globalThis.localStorage?.getItem(ADMIN_TOKEN_STORAGE_KEY)).not.toBe('secret')
  })

  it('uploads multiple PDFs and selected model keys as multipart data', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response(JSON.stringify({ batch_id: 'batch-1', job_ids: ['job-1'], batch: {} }), { status: 200 }),
    )
    const first = new File([new Uint8Array([37, 80, 68, 70])], 'first.pdf', { type: 'application/pdf' })
    const second = new File([new Uint8Array([37, 80, 68, 70])], 'second.pdf', { type: 'application/pdf' })

    await createPipelineBatch(
      { pdfs: [first, second], bibtex: null, models: { ocr: 'ocr-a', extract: 'extract-a', translate: 'translate-a' } },
      'secret',
    )

    const [, init] = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0] as [string, RequestInit]
    expect(init.headers).toEqual({ Authorization: 'Bearer secret' })
    expect(init.body).toBeInstanceOf(FormData)
    const body = init.body as FormData
    expect(body.getAll('pdfs[]')).toHaveLength(2)
    expect(body.get('ocr_model')).toBe('ocr-a')
    expect(body.get('extract_model')).toBe('extract-a')
    expect(body.get('translate_model')).toBe('translate-a')
  })

  it('fetches protected artifacts with auth and returns response for Blob preview', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('%PDF-1.7', { status: 200, headers: { 'Content-Type': 'application/pdf' } }),
    )

    const response = await fetchAdminArtifact('secret', 'job-1', 'pdf')

    expect(response.headers.get('content-type')).toContain('application/pdf')
    expect((globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0]?.[1]).toMatchObject({
      headers: { Authorization: 'Bearer secret' },
    })
  })
})
