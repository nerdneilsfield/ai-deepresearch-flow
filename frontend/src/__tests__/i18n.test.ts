import { beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.resetModules()
  Object.defineProperty(window, 'localStorage', {
    configurable: true,
    value: {
      getItem: () => null,
      setItem: () => undefined,
    },
  })
})

describe('P2P ICE translation', () => {
  it('renders literal JSON examples in both supported languages', async () => {
    const { default: i18n } = await import('@/i18n')
    const originalLocale = i18n.global.locale.value

    try {
      i18n.global.locale.value = 'en'
      expect(i18n.global.t('p2pIcePlaceholder')).toContain('{ "urls": ["stun:stun.example.net:3478"] }')

      i18n.global.locale.value = 'zh'
      expect(i18n.global.t('p2pIcePlaceholder')).toContain('{ "urls": ["stun:stun.example.net:3478"] }')
    } finally {
      i18n.global.locale.value = originalLocale
    }
  })
})
