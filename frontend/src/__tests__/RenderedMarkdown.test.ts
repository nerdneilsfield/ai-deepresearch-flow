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
    expect(document.querySelector('script[src*="unpkg.com/@highlightjs"]')).toBeNull()
    expect(document.querySelector('link[href*="unpkg.com/@highlightjs"]')).toBeNull()

    wrapper.unmount()
  })

  it('renders markdown when browser storage is unavailable', async () => {
    const localStorageDescriptor = Object.getOwnPropertyDescriptor(window, 'localStorage')
    try {
      Object.defineProperty(window, 'localStorage', {
        configurable: true,
        get() {
          throw new Error('storage blocked')
        },
      })

      const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
      const wrapper = mount(RenderedMarkdown, {
        attachTo: document.body,
        props: {
          markdown: '# Storage independent',
        },
      })

      await new Promise((resolve) => setTimeout(resolve, 700))
      await wrapper.vm.$nextTick()

      expect(wrapper.text()).toContain('Storage independent')

      wrapper.unmount()
    } finally {
      if (localStorageDescriptor) {
        Object.defineProperty(window, 'localStorage', localStorageDescriptor)
      }
    }
  })

  it('keeps API formula tags visible or rendered and reports useful diagnostics on failure', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const source = [
      'FORMULA_BEFORE <inline-formula><tex-math>x_{api_sentinel}=42</tex-math></inline-formula> FORMULA_AFTER',
    ].join('\n')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: { markdown: source },
    })

    await new Promise((resolve) => setTimeout(resolve, 900))
    await wrapper.vm.$nextTick()

    const html = wrapper.html()
    const text = wrapper.text()
    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${text.slice(0, 500)}`,
      `html=${html.slice(0, 1200)}`,
      `katex=${wrapper.findAll('.katex').length}`,
      `diagnostics=${diagnosticPanel.exists() ? diagnosticPanel.text() : '<none>'}`,
    ].join('\n')

    expect(text, diagnostics).toContain('FORMULA_BEFORE')
    expect(text, diagnostics).toContain('FORMULA_AFTER')
    expect(
      wrapper.findAll('.md-editor-katex-inline[data-processed], .md-editor-katex-block[data-processed]').length,
      diagnostics,
    ).toBeGreaterThan(0)
    expect(diagnosticPanel.exists(), diagnostics).toBe(false)

    wrapper.unmount()
  })

  it('shows detailed diagnostics when Mermaid input remains unrendered', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const source = [
      'Diagram before.',
      '',
      '```mermaid',
      'graph TD',
      '  api_source --> api_target',
      '```',
      '',
      'Diagram after.',
    ].join('\n')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: { markdown: source },
    })

    await new Promise((resolve) => setTimeout(resolve, 1200))
    await wrapper.vm.$nextTick()

    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${wrapper.text().slice(0, 800)}`,
      `html=${wrapper.html().slice(0, 1600)}`,
      `mermaidNodes=${wrapper.findAll('.md-editor-mermaid').length}`,
      `processedMermaid=${wrapper.findAll('.md-editor-mermaid[data-processed]').length}`,
    ].join('\n')

    expect(wrapper.text(), diagnostics).toContain('Diagram before.')
    expect(wrapper.text(), diagnostics).toContain('Diagram after.')
    expect(diagnosticPanel.exists(), diagnostics).toBe(true)
    expect(diagnosticPanel.text(), diagnostics).toContain('Mermaid')
    expect(diagnosticPanel.text(), diagnostics).toContain('api_source')
    expect(diagnosticPanel.text(), diagnostics).toContain('api_target')

    wrapper.unmount()
  })

  it('preserves Mermaid HTML line breaks inside node labels for rendering', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const source = [
      'Diagram before.',
      '',
      '```mermaid',
      'flowchart TD',
      '  A["alpha<br/>beta"] --> B["gamma<br/>delta"]',
      '```',
      '',
      'Diagram after.',
    ].join('\n')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: { markdown: source },
    })

    await new Promise((resolve) => setTimeout(resolve, 1200))
    await wrapper.vm.$nextTick()

    const html = wrapper.html()
    const text = wrapper.text()
    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${text.slice(0, 1000)}`,
      `html=${html.slice(0, 2000)}`,
      `diagnostics=${diagnosticPanel.exists() ? diagnosticPanel.text() : '<none>'}`,
    ].join('\n')

    expect(text, diagnostics).toContain('Diagram before.')
    expect(text, diagnostics).toContain('Diagram after.')
    expect(html, diagnostics).toContain('alpha&lt;br/&gt;beta')
    expect(html, diagnostics).toContain('gamma&lt;br/&gt;delta')

    wrapper.unmount()
  })

  it('converts Mermaid HTML line breaks outside labels into real line breaks', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const source = [
      'Diagram before.',
      '',
      '```mermaid',
      'flowchart TD<br/>  A --> B',
      '```',
      '',
      'Diagram after.',
    ].join('\n')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: { markdown: source },
    })

    await new Promise((resolve) => setTimeout(resolve, 1200))
    await wrapper.vm.$nextTick()

    const html = wrapper.html()
    const text = wrapper.text()
    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${text.slice(0, 1000)}`,
      `html=${html.slice(0, 2000)}`,
      `diagnostics=${diagnosticPanel.exists() ? diagnosticPanel.text() : '<none>'}`,
    ].join('\n')

    expect(text, diagnostics).toContain('Diagram before.')
    expect(text, diagnostics).toContain('Diagram after.')
    expect(html, diagnostics).toContain('flowchart TD')
    expect(html, diagnostics).toContain('A --&gt; B')
    expect(html, diagnostics).not.toContain('TD&lt;br/&gt;')

    wrapper.unmount()
  })

  it('treats naked flowchart source as a Mermaid diagram', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const source = [
      'Diagram before.',
      '',
      'flowchart TD',
      '  api_source --> api_target',
      '',
      'Diagram after.',
    ].join('\n')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: { markdown: source },
    })

    await new Promise((resolve) => setTimeout(resolve, 1200))
    await wrapper.vm.$nextTick()

    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${wrapper.text().slice(0, 1000)}`,
      `html=${wrapper.html().slice(0, 2000)}`,
      `diagnostics=${diagnosticPanel.exists() ? diagnosticPanel.text() : '<none>'}`,
    ].join('\n')

    expect(wrapper.text(), diagnostics).toContain('Diagram before.')
    expect(wrapper.text(), diagnostics).toContain('Diagram after.')
    expect(diagnosticPanel.exists(), diagnostics).toBe(true)
    expect(diagnosticPanel.text(), diagnostics).toContain('Mermaid')
    expect(diagnosticPanel.text(), diagnostics).toContain('flowchart TD')
    expect(diagnosticPanel.text(), diagnostics).toContain('api_source')
    expect(diagnosticPanel.text(), diagnostics).toContain('api_target')

    wrapper.unmount()
  })
})

it('falls back to plain text for oversized markdown instead of invoking rich renderers', async () => {
  const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
  const wrapper = mount(RenderedMarkdown, {
    attachTo: document.body,
    props: {
      markdown: `# Huge\n\n${'x'.repeat(600_000)}`,
    },
  })

  expect(wrapper.text()).toContain('Markdown is too large')
  expect(wrapper.find('pre').text()).toContain('Huge')
  expect(wrapper.text()).toContain('preview was truncated')
  const outlineEvents = wrapper.emitted('outline') ?? []
  expect(outlineEvents[outlineEvents.length - 1]?.[0]).toEqual([])

  wrapper.unmount()
})
