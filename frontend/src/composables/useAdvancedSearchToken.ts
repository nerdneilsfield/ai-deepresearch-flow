import { ref, type Ref } from 'vue'

import * as advancedSearchApi from '@/lib/advanced-search'
import { clearToken, getToken, setToken } from '@/lib/token-db'

export type TokenState = 'not-verified' | 'verifying' | 'verified'

export interface AdvancedSearchTokenAPI {
  state: Ref<TokenState>
  token: Ref<string | null>
  failureReason: Ref<'invalid' | null>
  hydrate: () => Promise<void>
  verify: (candidate: string) => Promise<boolean>
  clear: () => Promise<void>
  onAuthFailure: () => Promise<void>
}

const _state = ref<TokenState>('not-verified')
const _token = ref<string | null>(null)
const _failureReason = ref<'invalid' | null>(null)
let _opVersion = 0
let _storageMutation: Promise<void> = Promise.resolve()

function enqueueStorageMutation(operation: () => Promise<void>): Promise<void> {
  const next = _storageMutation.catch(() => undefined).then(operation)
  _storageMutation = next.catch(() => undefined)
  return next
}

export function useAdvancedSearchToken(): AdvancedSearchTokenAPI {
  async function hydrate(): Promise<void> {
    const opVersion = ++_opVersion
    _failureReason.value = null
    const stored = await getToken()
    if (opVersion !== _opVersion) return
    if (!stored) {
      _state.value = 'not-verified'
      _token.value = null
      return
    }
    _state.value = 'verifying'
    _token.value = stored
    try {
      const result = await advancedSearchApi.verifyToken(stored)
      if (opVersion !== _opVersion) return
      if (result.valid) {
        _failureReason.value = null
        _state.value = 'verified'
        _token.value = stored
      } else {
        _failureReason.value = 'invalid'
        _token.value = null
        _state.value = 'not-verified'
        await enqueueStorageMutation(async () => {
          if (opVersion === _opVersion) await clearToken()
        })
      }
    } catch {
      if (opVersion !== _opVersion) return
      _state.value = 'not-verified'
    }
  }

  async function verify(candidate: string): Promise<boolean> {
    const opVersion = ++_opVersion
    const previousState = _state.value
    const previousToken = _token.value
    _failureReason.value = null
    _state.value = 'verifying'
    try {
      const result = await advancedSearchApi.verifyToken(candidate)
      if (opVersion !== _opVersion) return false
      if (result.valid) {
        _token.value = candidate
        _failureReason.value = null
        _state.value = 'verified'
        await enqueueStorageMutation(async () => {
          if (opVersion === _opVersion) await setToken(candidate)
        })
        return opVersion === _opVersion
      }
      _token.value = null
      _failureReason.value = 'invalid'
      _state.value = 'not-verified'
      await enqueueStorageMutation(async () => {
        if (opVersion === _opVersion) await clearToken()
      })
      return false
    } catch (error) {
      if (opVersion !== _opVersion) return false
      _token.value = previousToken
      _failureReason.value = null
      _state.value = previousState
      throw error
    }
  }

  async function clear(): Promise<void> {
    _opVersion += 1
    _token.value = null
    _failureReason.value = null
    _state.value = 'not-verified'
    await enqueueStorageMutation(clearToken)
  }

  async function onAuthFailure(): Promise<void> {
    await clear()
  }

  return {
    state: _state,
    token: _token,
    failureReason: _failureReason,
    hydrate,
    verify,
    clear,
    onAuthFailure,
  }
}
