import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
import * as tokenDb from '@/lib/token-db'
import * as api from '@/lib/advanced-search'

beforeEach(async () => {
  await tokenDb.clearToken()
  vi.restoreAllMocks()
  await useAdvancedSearchToken().clear()
})

afterEach(async () => {
  await tokenDb.clearToken()
})

describe('useAdvancedSearchToken', () => {
  it('starts in not-verified', () => {
    const token = useAdvancedSearchToken()
    expect(token.state.value).toBe('not-verified')
    expect(token.token.value).toBeNull()
  })

  it('hydrate with no stored token stays not-verified', async () => {
    const token = useAdvancedSearchToken()
    await token.hydrate()
    expect(token.state.value).toBe('not-verified')
  })

  it('hydrate with valid stored token → verified', async () => {
    await tokenDb.setToken('good')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const token = useAdvancedSearchToken()
    await token.hydrate()
    expect(token.state.value).toBe('verified')
    expect(token.token.value).toBe('good')
  })

  it('hydrate with invalid stored token → not-verified + cleared', async () => {
    await tokenDb.setToken('bad')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: false, reason: 'invalid' })
    const token = useAdvancedSearchToken()
    await token.hydrate()
    expect(token.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('hydrate with transient verify failure keeps stored token for retry', async () => {
    await tokenDb.setToken('saved')
    vi.spyOn(api, 'verifyToken').mockRejectedValueOnce(new Error('server down'))
    const token = useAdvancedSearchToken()
    await token.hydrate()
    expect(token.state.value).toBe('not-verified')
    expect(token.token.value).toBe('saved')
    expect(await tokenDb.getToken()).toBe('saved')
  })

  it('verify valid token → verified and stores', async () => {
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const token = useAdvancedSearchToken()
    expect(await token.verify('abc')).toBe(true)
    expect(token.state.value).toBe('verified')
    expect(await tokenDb.getToken()).toBe('abc')
  })

  it('verify invalid token → not-verified and clears', async () => {
    await tokenDb.setToken('previous')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: false, reason: 'invalid' })
    const token = useAdvancedSearchToken()
    expect(await token.verify('wrong')).toBe(false)
    expect(token.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('verify transient error preserves existing verified token', async () => {
    await tokenDb.setToken('live')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const token = useAdvancedSearchToken()
    await token.hydrate()
    vi.spyOn(api, 'verifyToken').mockRejectedValueOnce(new Error('temporary'))
    await expect(token.verify('candidate')).rejects.toThrow('temporary')
    expect(token.state.value).toBe('verified')
    expect(token.token.value).toBe('live')
    expect(await tokenDb.getToken()).toBe('live')
  })

  it('onAuthFailure clears stored token and flips state', async () => {
    await tokenDb.setToken('live')
    vi.spyOn(api, 'verifyToken').mockResolvedValueOnce({ valid: true })
    const token = useAdvancedSearchToken()
    await token.hydrate()
    await token.onAuthFailure()
    expect(token.state.value).toBe('not-verified')
    expect(await tokenDb.getToken()).toBeNull()
  })

  it('state is verifying during in-flight verify()', async () => {
    let resolveVerify: (value: { valid: boolean; reason?: 'missing' | 'invalid' }) => void = () => {}
    vi.spyOn(api, 'verifyToken').mockReturnValueOnce(new Promise((resolve) => {
      resolveVerify = resolve as typeof resolveVerify
    }))
    const token = useAdvancedSearchToken()
    const pending = token.verify('x')
    expect(token.state.value).toBe('verifying')
    resolveVerify({ valid: true })
    await pending
    expect(token.state.value).toBe('verified')
  })
})
