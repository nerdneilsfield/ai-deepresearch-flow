import { mount } from '@vue/test-utils'
import { defineComponent, h, onMounted } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

const previewStubState = vi.hoisted(() => ({
  mode: 'strip-content' as 'strip-content' | 'hidden-katex' | 'throw',
}))

vi.mock('@/composables/useTheme', () => ({
  useTheme: () => ({ themeMode: { value: 'light' } }),
}))

vi.mock('md-editor-v3', () => ({
  config: vi.fn(),
  MdPreview: defineComponent({
    name: 'MdPreviewDiagnosticsStub',
    props: {
      editorId: {
        type: String,
        required: true,
      },
      modelValue: {
        type: String,
        default: '',
      },
    },
    emits: ['onHtmlChanged', 'onGetCatalog'],
    setup(props, { emit }) {
      onMounted(() => {
        emit('onHtmlChanged')
      })

      return () => {
        if (previewStubState.mode === 'throw') {
          throw new Error('preview renderer exploded')
        }
        if (previewStubState.mode === 'hidden-katex') {
          return h('div', { id: props.editorId }, [
            h('p', [
              'Renderer returned a KaTeX node without the visibility marker: ',
              h('span', { class: 'md-editor-katex-inline' }, [
                h('span', { class: 'katex' }, 'x_hidden_by_sanitizer'),
              ]),
            ]),
          ])
        }
        return h('div', { id: props.editorId }, [
          h('p', 'Renderer returned visible prose but no formulas or diagrams.'),
        ])
      }
    },
  }),
}))

describe('RenderedMarkdown diagnostics', () => {
  afterEach(() => {
    previewStubState.mode = 'strip-content'
    document.head.innerHTML = ''
    document.body.innerHTML = ''
    vi.clearAllMocks()
  })

  it('reports the source excerpt when formulas disappear from rendered output', async () => {
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: {
        markdown: 'Before $x_{lost_formula}=1$ after.',
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 250))
    await wrapper.vm.$nextTick()

    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${wrapper.text()}`,
      `html=${wrapper.html()}`,
    ].join('\n')

    expect(diagnosticPanel.exists(), diagnostics).toBe(true)
    expect(diagnosticPanel.text(), diagnostics).toContain('Math')
    expect(diagnosticPanel.text(), diagnostics).toContain('lost_formula')

    wrapper.unmount()
  })

  it('reports hidden formulas when rendered KaTeX lost its visibility marker', async () => {
    previewStubState.mode = 'hidden-katex'
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')
    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: {
        markdown: 'Before $x_{hidden_by_sanitizer}=1$ after.',
      },
    })

    await new Promise((resolve) => setTimeout(resolve, 250))
    await wrapper.vm.$nextTick()

    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${wrapper.text()}`,
      `html=${wrapper.html()}`,
    ].join('\n')

    expect(diagnosticPanel.exists(), diagnostics).toBe(true)
    expect(diagnosticPanel.text(), diagnostics).toContain('Math')
    expect(diagnosticPanel.text(), diagnostics).toContain('hidden_by_sanitizer')

    wrapper.unmount()
  })

  it('reports renderer exceptions with the original markdown excerpt', async () => {
    previewStubState.mode = 'throw'
    const { default: RenderedMarkdown } = await import('@/components/RenderedMarkdown.vue')

    const wrapper = mount(RenderedMarkdown, {
      attachTo: document.body,
      props: {
        markdown: 'Renderer exception source sentinel.',
      },
    })

    await wrapper.vm.$nextTick()

    const diagnosticPanel = wrapper.find('[data-testid="markdown-renderer-diagnostics"]')
    const diagnostics = [
      `text=${wrapper.text()}`,
      `html=${wrapper.html()}`,
    ].join('\n')

    expect(diagnosticPanel.exists(), diagnostics).toBe(true)
    expect(diagnosticPanel.text(), diagnostics).toContain('Markdown renderer threw an exception')
    expect(diagnosticPanel.text(), diagnostics).toContain('preview renderer exploded')
    expect(diagnosticPanel.text(), diagnostics).toContain('Renderer exception source sentinel')

    wrapper.unmount()
  })
})
