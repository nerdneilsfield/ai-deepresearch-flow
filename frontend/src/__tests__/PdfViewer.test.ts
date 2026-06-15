import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

vi.mock('@tuttarealstep/vue-pdf.js', () => ({
  VuePDFjs: defineComponent({
    name: 'VuePDFjs',
    props: ['source', 'options', 'sourceOptions'],
    setup(props) {
      return () => {
        if (String(props.source).includes('worker-failure')) {
          props.sourceOptions?.onError?.(new Error('worker unavailable'))
        }
        return h('div', { 'data-testid': 'pdfjs', 'data-source': props.source })
      }
    },
  }),
}))
vi.mock('@tuttarealstep/vue-pdf.js/dist/style.css', () => ({}))
vi.mock('@tuttarealstep/vue-pdf.js/l10n/en-US/viewer.ftl?raw', () => ({ default: 'locale' }))

describe('PdfViewer', () => {
  it('shows an empty state when no PDF URL is available', async () => {
    const { default: PdfViewer } = await import('@/components/PdfViewer.vue')
    const wrapper = mount(PdfViewer, { props: { url: null } })

    expect(wrapper.text()).toContain('No PDF available')
  })

  it('passes the selected PDF URL to the viewer component', async () => {
    const { default: PdfViewer } = await import('@/components/PdfViewer.vue')
    const wrapper = mount(PdfViewer, { props: { url: 'https://example.test/paper.pdf' } })

    expect(wrapper.find('[data-testid="pdfjs"]').attributes('data-source')).toBe('https://example.test/paper.pdf')
  })

  it('shows a local fallback when the PDF worker fails', async () => {
    const { default: PdfViewer } = await import('@/components/PdfViewer.vue')
    const wrapper = mount(PdfViewer, { props: { url: 'https://example.test/worker-failure.pdf' } })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('PDF viewer is unavailable')
    expect(wrapper.text()).toContain('worker unavailable')
  })
})
