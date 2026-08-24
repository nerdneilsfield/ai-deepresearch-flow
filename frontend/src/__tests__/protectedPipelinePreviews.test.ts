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
})
