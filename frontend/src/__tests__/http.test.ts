import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fetchResponse } from '@/lib/http'

const originalFetch = globalThis.fetch

beforeEach(() => {
  globalThis.fetch = vi.fn() as unknown as typeof fetch
})

afterEach(() => {
  vi.useRealTimers()
  globalThis.fetch = originalFetch
})

describe('fetchResponse', () => {
  it('stops retry backoff immediately when the caller aborts', async () => {
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValueOnce(
      new Response('', { status: 503 }),
    )
    const controller = new AbortController()

    const promise = fetchResponse('/retry', { retry: 1, signal: controller.signal })
    await Promise.resolve()
    controller.abort(new DOMException('stopped', 'AbortError'))

    await expect(promise).rejects.toBeInstanceOf(DOMException)
    expect(globalThis.fetch).toHaveBeenCalledTimes(1)
  })

  it('retries after an internal request timeout', async () => {
    vi.useFakeTimers()
    ;(globalThis.fetch as unknown as ReturnType<typeof vi.fn>)
      .mockImplementationOnce((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(init.signal?.reason), { once: true })
      }))
      .mockResolvedValueOnce(new Response('ok', { status: 200 }))

    const promise = fetchResponse('/timeout', { timeoutMs: 10, retry: 1 })
    await vi.advanceTimersByTimeAsync(10)
    await vi.advanceTimersByTimeAsync(600)

    const response = await promise
    expect(response.status).toBe(200)
    expect(globalThis.fetch).toHaveBeenCalledTimes(2)
  })
})
