import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const { getTranslatedMarkdownCachedMock, fetchTextMock } = vi.hoisted(() => ({
  getTranslatedMarkdownCachedMock: vi.fn(),
  fetchTextMock: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  getTranslatedMarkdownCached: getTranslatedMarkdownCachedMock,
  fetchText: fetchTextMock,
}))

vi.mock('@/components/MarkdownContent.vue', () => ({
  default: {
    name: 'MarkdownContent',
    props: ['markdown'],
    template: '<div data-testid="markdown-content">{{ markdown }}</div>',
  },
}))

describe('MarkdownPanel', () => {
  beforeEach(() => {
    getTranslatedMarkdownCachedMock.mockReset()
    fetchTextMock.mockReset()
  })

  it('loads translated markdown through the shared translation cache wrapper', async () => {
    getTranslatedMarkdownCachedMock.mockResolvedValueOnce('cached translated markdown')

    const { default: MarkdownPanel } = await import('@/components/MarkdownPanel.vue')
    const wrapper = mount(MarkdownPanel, {
      props: {
        url: 'https://example.com/md_translate/zh/paper-1-zh.md',
        cachePaperId: 'paper-1',
        cacheTranslationLang: 'zh',
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 0))
    await wrapper.vm.$nextTick()

    expect(getTranslatedMarkdownCachedMock).toHaveBeenCalledWith(
      'paper-1',
      'zh',
      'https://example.com/md_translate/zh/paper-1-zh.md',
    )
    expect(fetchTextMock).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('cached translated markdown')
  })
})
