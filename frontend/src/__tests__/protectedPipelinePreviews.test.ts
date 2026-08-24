import { defineComponent, h, nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { useProtectedPipelinePreviews } from '@/composables/useProtectedPipelinePreviews'

describe('protected pipeline previews', () => {
  const originalFetch = globalThis.fetch
  const originalCreateObjectURL = URL.createObjectURL
  const originalRevokeObjectURL = URL.revokeObjectURL

  beforeEach(() => {
    globalThis.fetch = vi.fn().mockImplementation(() => Promise.resolve(
      new Response('preview', { status: 200 }),
    )) as unknown as typeof fetch
    URL.createObjectURL = vi.fn().mockReturnValue('blob:protected-preview')
    URL.revokeObjectURL = vi.fn()
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    URL.createObjectURL = originalCreateObjectURL
    URL.revokeObjectURL = originalRevokeObjectURL
  })

  it('fetches every preview with bearer auth and revokes PDF object URL on unmount', async () => {
    let load: ((jobId: string, token: string) => Promise<void>) | undefined
    const Host = defineComponent({
      setup() {
        const previews = useProtectedPipelinePreviews()
        load = previews.load
        return () => h('div')
      },
    })
    const wrapper = mount(Host)
    await load?.('job-1', 'session-secret')
    await nextTick()

    const calls = (globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mock.calls
    expect(calls).toHaveLength(4)
    expect(calls.every(([, init]) => (init as RequestInit).headers).valueOf()).toBe(true)
    expect((calls[0]?.[1] as RequestInit).headers).toEqual({ Authorization: 'Bearer session-secret' })
    wrapper.unmount()
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:protected-preview')
  })

  it('revokes a newly created PDF URL when later text decoding fails', async () => {
    const pdf = { ok: true, blob: () => Promise.resolve(new Blob(['%PDF-1.7'])) }
    const brokenText = { ok: true, text: () => Promise.reject(new Error('summary decode failed')) }
    const text = { ok: true, text: () => Promise.resolve('# text') }
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce(pdf)
      .mockResolvedValueOnce(text)
      .mockResolvedValueOnce(brokenText)
      .mockResolvedValueOnce(text) as unknown as typeof fetch

    let load: ((jobId: string, token: string) => Promise<void>) | undefined
    const Host = defineComponent({
      setup() {
        const previews = useProtectedPipelinePreviews()
        load = previews.load
        return () => h('div')
      },
    })
    const wrapper = mount(Host)
    await load?.('job-1', 'session-secret')

    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:protected-preview')
    wrapper.unmount()
  })

  it('fences late prior loads and revokes their object URL', async () => {
    let firstResolve: ((value: unknown) => void)[] = []
    let secondResolve: ((value: unknown) => void)[] = []
    const deferred = (bucket: ((value: unknown) => void)[]) => new Promise<unknown>((resolve) => bucket.push(resolve))
    globalThis.fetch = vi.fn()
      .mockImplementationOnce(() => deferred(firstResolve))
      .mockImplementationOnce(() => deferred(firstResolve))
      .mockImplementationOnce(() => deferred(firstResolve))
      .mockImplementationOnce(() => deferred(firstResolve))
      .mockImplementationOnce(() => deferred(secondResolve))
      .mockImplementationOnce(() => deferred(secondResolve))
      .mockImplementationOnce(() => deferred(secondResolve))
      .mockImplementationOnce(() => deferred(secondResolve)) as unknown as typeof fetch
    let nextUrl = 0
    URL.createObjectURL = vi.fn().mockImplementation(() => `blob:load-${++nextUrl}`)
    let load: ((jobId: string, token: string) => Promise<void>) | undefined
    let pdfUrl: { value: string | null } | undefined
    const Host = defineComponent({
      setup() {
        const previews = useProtectedPipelinePreviews()
        load = previews.load
        pdfUrl = previews.pdfUrl
        return () => h('div')
      },
    })
    const wrapper = mount(Host)
    const firstLoad = load?.('first', 'session-secret')
    const secondLoad = load?.('second', 'session-secret')
    const responses = () => ({
      ok: true,
      blob: () => Promise.resolve(new Blob(['%PDF-1.7'])),
      text: () => Promise.resolve('# text'),
    })
    firstResolve.forEach((resolve) => resolve(responses()))
    secondResolve.forEach((resolve) => resolve(responses()))
    await Promise.all([firstLoad, secondLoad])

    expect(pdfUrl?.value).toBe('blob:load-2')
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:load-1')
    wrapper.unmount()
  })
})
