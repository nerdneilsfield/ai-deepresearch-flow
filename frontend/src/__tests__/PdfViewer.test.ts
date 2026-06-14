import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tuttarealstep/vue-pdf.js', () => ({
  VuePDFjs: {
    name: 'VuePDFjs',
    props: ['source', 'options'],
    template: '<div data-testid="pdfjs" :data-source="source"></div>',
  },
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
})
