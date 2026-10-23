import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

const global = {}

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
    const wrapper = mount(PdfViewer, { props: { url: null }, global })

    expect(wrapper.text()).toContain('pdfUnavailable')
  })

  it('passes the selected PDF URL to the viewer component', async () => {
    const { default: PdfViewer } = await import('@/components/PdfViewer.vue')
    const wrapper = mount(PdfViewer, { props: { url: 'https://example.test/paper.pdf' }, global })

    expect(wrapper.find('[data-testid="pdfjs"]').attributes('data-source')).toBe('https://example.test/paper.pdf')
  })

  it('shows a local fallback when the PDF worker fails', async () => {
    const { default: PdfViewer } = await import('@/components/PdfViewer.vue')
    const wrapper = mount(PdfViewer, { props: { url: 'https://example.test/worker-failure.pdf' }, global })
    await wrapper.vm.$nextTick()

    expect(wrapper.text()).toContain('pdfLoadFailed')
    expect(wrapper.text()).toContain('worker unavailable')
  })
})
