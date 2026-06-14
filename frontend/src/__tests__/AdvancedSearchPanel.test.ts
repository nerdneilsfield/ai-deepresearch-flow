import 'fake-indexeddb/auto'
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import * as tokenDb from '@/lib/token-db'

type VerifyResultLike = { valid: true } | { valid: false; reason: 'missing' | 'invalid' }

const { verifyTokenMock } = vi.hoisted(() => ({
  verifyTokenMock: vi.fn<(token: string) => Promise<VerifyResultLike>>(),
}))
const { pushToastMock } = vi.hoisted(() => ({
  pushToastMock: vi.fn(),
}))

vi.mock('@/lib/advanced-search', () => ({
  verifyToken: verifyTokenMock,
}))
vi.mock('@/stores/ui', () => ({
  useUiStore: () => ({
    pushToast: pushToastMock,
  }),
}))

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

  it('search button disabled when not verified', async () => {
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    expect((wrapper.find('[data-testid="advanced-search-button"]').element as HTMLButtonElement).disabled).toBe(true)
  })

  it('verify then search emits event', async () => {
    verifyTokenMock.mockResolvedValueOnce({ valid: true })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel)
    await flushPromises()
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    await wrapper.find('[data-testid="advanced-query-input"]').setValue('vision transformer')
    await wrapper.find('[data-testid="advanced-search-button"]').trigger('click')
    const events = wrapper.emitted('search')
    expect(events).toBeTruthy()
    expect((events as unknown[][])[0]?.[0]).toMatchObject({ q: 'vision transformer' })
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
    expect((wrapper.find('[data-testid="advanced-search-button"]').element as HTMLButtonElement).disabled).toBe(true)
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
    expect((wrapper.find('[data-testid="advanced-token-input"]').element as HTMLInputElement).value).toBe('stored-good')
    expect((wrapper.find('[data-testid="advanced-query-input"]').element as HTMLInputElement).disabled).toBe(false)
  })

  it('searching prop puts button in loading state and disables it', async () => {
    verifyTokenMock.mockResolvedValueOnce({ valid: true })
    const { default: AdvancedSearchPanel } = await import('@/components/AdvancedSearchPanel.vue')
    const wrapper = mount(AdvancedSearchPanel, { props: { searching: false } })
    await flushPromises()
    await wrapper.find('[data-testid="advanced-panel-toggle"]').trigger('click')
    await wrapper.find('[data-testid="advanced-token-input"]').setValue('secret')
    await wrapper.find('[data-testid="advanced-verify-button"]').trigger('click')
    await flushPromises()
    await wrapper.vm.$nextTick()
    await wrapper.setProps({ searching: true })
    const button = wrapper.find('[data-testid="advanced-search-button"]')
    expect((button.element as HTMLButtonElement).disabled).toBe(true)
    expect(button.text()).toContain('Searching')
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
      'Token verification failed. Please try again.',
      'error',
    )
    expect((wrapper.find('[data-testid="advanced-token-input"]').element as HTMLInputElement).value).toBe('secret')
    expect(wrapper.find('[data-testid="advanced-token-status-invalid"]').exists()).toBe(false)
  })
})
