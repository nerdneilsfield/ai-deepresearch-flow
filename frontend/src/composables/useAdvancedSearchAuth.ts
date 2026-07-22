import { computed, ref, type ComputedRef, type Ref } from 'vue'

import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
import {
  buildGitHubLoginUrl,
  getAdvancedSearchAuthConfig,
  getAdvancedSearchSession,
  logoutAdvancedSearchSession,
  type AdvancedSearchAuthMethod,
} from '@/lib/advanced-search'

export interface AdvancedSearchUser {
  id: string
  login: string
}

export interface AdvancedSearchAuthAPI {
  authenticated: ComputedRef<boolean>
  authMethods: Ref<AdvancedSearchAuthMethod[]>
  oauthUser: Ref<AdvancedSearchUser | null>
  token: Ref<string | null>
  tokenState: Ref<'not-verified' | 'verifying' | 'verified'>
  tokenFailureReason: Ref<'invalid' | null>
  hydrate: () => Promise<void>
  verifyToken: (candidate: string) => Promise<boolean>
  clearToken: () => Promise<void>
  logoutOAuth: () => Promise<void>
  refreshAfterFailure: () => Promise<void>
  githubLoginUrl: (returnTo: string) => string
}

const _authMethods = ref<AdvancedSearchAuthMethod[]>(['bearer'])
const _oauthUser = ref<AdvancedSearchUser | null>(null)
let _hydrateVersion = 0

export function useAdvancedSearchAuth(): AdvancedSearchAuthAPI {
  const tokenAuth = useAdvancedSearchToken()
  const authenticated = computed(
    () =>
      (_authMethods.value.includes('bearer') && tokenAuth.state.value === 'verified')
      || (_authMethods.value.includes('github-oauth') && _oauthUser.value !== null),
  )

  async function hydrate(): Promise<void> {
    const version = ++_hydrateVersion
    let methods: AdvancedSearchAuthMethod[] = ['bearer']
    try {
      methods = (await getAdvancedSearchAuthConfig()).authMethods
    } catch {
      // Older or temporarily unavailable backends retain bearer-token compatibility.
    }
    if (version !== _hydrateVersion) return
    _authMethods.value = methods

    const operations: Promise<void>[] = []
    if (methods.includes('bearer')) operations.push(tokenAuth.hydrate())
    if (methods.includes('github-oauth')) {
      operations.push(
        getAdvancedSearchSession()
          .then((session) => {
            if (version !== _hydrateVersion) return
            _oauthUser.value = session.authenticated ? session.user : null
          })
          .catch(() => {
            if (version === _hydrateVersion) _oauthUser.value = null
          }),
      )
    } else {
      _oauthUser.value = null
    }
    await Promise.all(operations)
  }

  async function logoutOAuth(): Promise<void> {
    await logoutAdvancedSearchSession()
    _oauthUser.value = null
  }

  async function refreshAfterFailure(): Promise<void> {
    await hydrate()
  }

  return {
    authenticated,
    authMethods: _authMethods,
    oauthUser: _oauthUser,
    token: tokenAuth.token,
    tokenState: tokenAuth.state,
    tokenFailureReason: tokenAuth.failureReason,
    hydrate,
    verifyToken: tokenAuth.verify,
    clearToken: tokenAuth.clear,
    logoutOAuth,
    refreshAfterFailure,
    githubLoginUrl: buildGitHubLoginUrl,
  }
}
