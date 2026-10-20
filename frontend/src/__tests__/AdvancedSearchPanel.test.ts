import 'fake-indexeddb/auto'
import { enableAutoUnmount, flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as tokenDb from '@/lib/token-db'

type VerifyResultLike = { valid: true } | { valid: false; reason: 'missing' | 'invalid' }

const { verifyTokenMock, authConfigMock, sessionMock, logoutMock } = vi.hoisted(() => ({
  verifyTokenMock: vi.fn<(token: string) => Promise<VerifyResultLike>>(),
  authConfigMock: vi.fn(),
  sessionMock: vi.fn(),
  logoutMock: vi.fn(),
}))
const { pushToastMock } = vi.hoisted(() => ({
  pushToastMock: vi.fn(),
}))

vi.mock('@/lib/advanced-search', () => ({
  verifyToken: verifyTokenMock,
  getAdvancedSearchAuthConfig: authConfigMock,
  getAdvancedSearchSession: sessionMock,
  logoutAdvancedSearchSession: logoutMock,
  buildGitHubLoginUrl: (returnTo: string) => `/api/v1/auth/github/login?return_to=${encodeURIComponent(returnTo)}`,
}))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string, params?: { login?: string }) => params?.login ? `${key}:${params.login}` : key }),
}))
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({
    pushToast: pushToastMock,
  }),
}))

enableAutoUnmount(afterEach)

async function settle(wrapper: ReturnType<typeof mount>) {
  await flushPromises()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await wrapper.vm.$nextTick()
}

beforeEach(async () => {
  await tokenDb.clearToken()
  vi.restoreAllMocks()
  verifyTokenMock.mockReset()
  verifyTokenMock.mockResolvedValue({ valid: false, reason: 'invalid' })
  authConfigMock.mockReset()
  authConfigMock.mockResolvedValue({ enabled: true, authMethods: ['bearer'], githubLoginUrl: null })
  sessionMock.mockReset()
  sessionMock.mockResolvedValue({ authenticated: false })
  logoutMock.mockReset()
  logoutMock.mockResolvedValue(undefined)
  pushToastMock.mockReset()
  const { useAdvancedSearchToken } = await import('@/composables/useAdvancedSearchToken')
  await useAdvancedSearchToken().clear()
})

afterEach(async () => {
  await tokenDb.clearToken()
})

describe('AdvancedSearchPanel', () => {
  it('starts collapsed', async () => {
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    expect(wrapper.find('[data-testid="advanced-panel-body"]').exists()).toBe(false)
  })

  it('expands on toggle', async () => {
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    expect(wrapper.find('[data-testid="advanced-panel-body"]').exists()).toBe(true)
  })

  it('renders expanded controls with dark-theme compatible surfaces', async () => {
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')

    expect(wrapper.find('[data-testid="advanced-panel-body"]').classes().some((name) => name.startsWith('dark:bg-'))).toBe(true)
    expect(wrapper.find('[data-testid="advanced-panel-toggle"]').classes().some((name) => name.startsWith('dark:text-'))).toBe(true)
    expect(wrapper.find('[data-testid="advanced-token-input"]').classes().some((name) => name.startsWith('dark:bg-'))).toBe(true)
  })

  it('verifying a token marks the panel as authenticated', async () => {
    verifyTokenMock.mockResolvedValueOnce({ valid: true })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await flushPromises()
    expect(wrapper.find('[data-testid="advanced-panel-authenticated"]').exists()).toBe(false)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="advanced-token-status-verified"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="advanced-panel-authenticated"]').exists()).toBe(true)
  })

  it('invalid token shows error indicator', async () => {
    verifyTokenMock.mockResolvedValueOnce({ valid: false, reason: 'invalid' })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await flushPromises()
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('bad')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="advanced-panel-authenticated"]').exists()).toBe(false)
  })

  it('verify button disabled for blank token input', async () => {
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await flushPromises()
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    expect((wrapper.find('[data-testid="advanced-verify-button"]').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('auto-verifies a stored token on mount (hydrate wiring)', async () => {
    await tokenDb.setToken('stored-good')
    verifyTokenMock.mockResolvedValue({ valid: true })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await vi.waitFor(() => {
      expect((wrapper.find('[data-testid="advanced-token-input"]').element as HTMLInputElement).value).toBe('stored-good')
      expect(wrapper.find('[data-testid="advanced-panel-authenticated"]').exists()).toBe(true)
    })
  })

  it('transient verify failure shows a toast and keeps the current input', async () => {
    verifyTokenMock.mockRejectedValueOnce(new Error('server down'))
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await flushPromises()
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(pushToastMock).toHaveBeenCalledWith(
      'advancedTokenVerifyFailed',
      'error',
    )
    expect((wrapper.find('[data-testid="advanced-token-input"]').element as HTMLInputElement).value).toBe('secret')
    expect(wrapper.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(false)
  })

  it('GitHub session enables search without a bearer token and can sign out', async () => {
    authConfigMock.mockResolvedValueOnce({
      enabled: true,
      authMethods: ['github-oauth', 'bearer'],
      githubLoginUrl: '/api/v1/auth/github/login',
    })
    sessionMock.mockResolvedValueOnce({
      authenticated: true,
      user: { id: '42', login: 'octocat' },
    })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await settle(wrapper)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')

    expect(wrapper.find('[data-testid="advanced-github-user"]').text()).toContain('octocat')
    expect(wrapper.find('[data-testid="advanced-panel-authenticated"]').exists()).toBe(true)

    await wrapper.find('[data-testid="advanced-github-logout"]').trigger('click')
    await settle(wrapper)
    expect(wrapper.find('[data-testid="advanced-github-login"]').exists()).toBe(true)
  })
})
