import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/composables/useTheme', () => ({
  useTheme: () => ({ themeMode: { value: 'light' } }),
}))

describe('RenderedMarkdown', () => {
  beforeEach(() => {
    document.head.innerHTML = ''
    document.body.innerHTML = ''
  })

  afterEach(() => {
    document.head.innerHTML = ''
    document.body.innerHTML = ''
  })

  it('mounts rich markdown without requesting remote Mermaid or KaTeX scripts', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: {
        markdown: [
          '# Render smoke',
          '',
          '- [x] task item',
          '',
          'Inline math $\\textcircled{1}$ and footnote[^1].',
          '',
          '```mermaid',
          'graph TD',
          '  A --> B',
          '```',
          '',
          '[^1]: note text',
        ].join('\n'),
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 700))
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('Render smoke')
    expect(wrapper.text()).toContain('task item')
    expect(document.querySelector('script[src*="unpkg.com/mermaid"]')).toBeNull()
    expect(document.querySelector('script[src*="unpkg.com/katex"]')).toBeNull()
    expect(document.querySelector('link[href*="unpkg.com/katex"]')).toBeNull()

    wrapper.unmount()
  })
})
