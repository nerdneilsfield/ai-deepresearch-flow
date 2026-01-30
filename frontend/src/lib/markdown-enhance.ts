import { lazyKatexAuto, lazyMarkmap, lazyMermaid } from './lazy'

type EnhanceOptions = {
  imagesBaseUrl?: string
  renderMath?: boolean
  renderMermaid?: boolean
  renderMarkmap?: boolean
}

function isAbsoluteUrl(value: string) {
  const lower = value.toLowerCase()
  return (
    lower.startsWith('http://') ||
    lower.startsWith('https://') ||
    lower.startsWith('data:') ||
    lower.startsWith('blob:') ||
    lower.startsWith('mailto:') ||
    lower.startsWith('file:') ||
    value.startsWith('#') ||
    value.startsWith('/')
  )
}

function normalizeBaseUrl(value?: string) {
  if (!value) return ''
  return value.replace(/\/+$/, '')
}

function rewriteImageSources(container: HTMLElement, imagesBaseUrl?: string) {
  const base = normalizeBaseUrl(imagesBaseUrl)
  if (!base) return
  container.querySelectorAll('img').forEach((img) => {
    const src = img.getAttribute('src') || ''
    if (!src || isAbsoluteUrl(src)) return
    let cleaned = src
    while (cleaned.startsWith('../')) {
      cleaned = cleaned.slice(3)
    }
    cleaned = cleaned.replace(/^\.\//, '')
    cleaned = cleaned.replace(/^\/+/, '')
    if (cleaned.startsWith('images/')) {
      cleaned = cleaned.slice('images/'.length)
    }
    img.setAttribute('src', `${base}/${cleaned}`)
  })
}

function extractFootnoteText(li: Element) {
  const clone = li.cloneNode(true) as HTMLElement
  clone.querySelectorAll('a.footnote-backref').forEach((el) => el.remove())
  return (clone.textContent || '').trim()
}

function initFootnoteTips(container: HTMLElement) {
  const footnotes = container.querySelector('.footnotes')
  if (!footnotes) return
  const notes = new Map<string, string>()
  footnotes.querySelectorAll('li[id]').forEach((li) => {
    const id = li.id.replace(/^fn/, '')
    if (!id) return
    notes.set(id, extractFootnoteText(li))
  })
  container.querySelectorAll('.footnote-ref a[href^="#fn"]').forEach((link) => {
    const href = link.getAttribute('href') || ''
    const id = href.replace(/^#fn/, '')
    const text = notes.get(id)
    if (!text) return
    link.setAttribute('data-footnote', text)
    link.classList.add('footnote-tip')
  })
}

function resizeMarkmap(svg: SVGSVGElement) {
  try {
    const bbox = svg.getBBox()
    if (!bbox || !bbox.height) {
      svg.style.height = '360px'
      svg.style.width = '100%'
      return
    }
    const height = Math.ceil(bbox.height * 1.6)
    svg.style.height = `${Math.max(height, 240)}px`
    if (bbox.width && bbox.width > svg.clientWidth) {
      svg.style.width = `${Math.ceil(bbox.width * 1.4)}px`
      if (svg.parentElement) svg.parentElement.style.overflowX = 'auto'
    } else {
      svg.style.width = '100%'
    }
  } catch {
    svg.style.height = '360px'
    svg.style.width = '100%'
  }
}

async function renderMarkmap(container: HTMLElement) {
  const blocks = Array.from(container.querySelectorAll('code.language-markmap'))
  if (!blocks.length) return
  const { Transformer, Markmap } = await lazyMarkmap()
  const transformer = new Transformer()
  const svgs: SVGSVGElement[] = []
  blocks.forEach((code) => {
    if ((code as HTMLElement).dataset.enhanced === 'true') return
    const pre = code.parentElement
    if (!pre) return
    const wrapper = document.createElement('div')
    wrapper.className = 'markmap'
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    wrapper.appendChild(svg)
    pre.replaceWith(wrapper)
    const { root } = transformer.transform(code.textContent || '')
    Markmap.create(svg, undefined, root)
    svgs.push(svg)
    ;(code as HTMLElement).dataset.enhanced = 'true'
  })
  requestAnimationFrame(() => svgs.forEach(resizeMarkmap))
}

async function renderMermaid(container: HTMLElement) {
  const blocks = Array.from(container.querySelectorAll('code.language-mermaid'))
  if (!blocks.length) return
  const mermaid = await lazyMermaid()
  blocks.forEach((code) => {
    if ((code as HTMLElement).dataset.enhanced === 'true') return
    const pre = code.parentElement
    if (!pre) return
    const div = document.createElement('div')
    div.className = 'mermaid'
    div.textContent = code.textContent || ''
    pre.replaceWith(div)
    ;(code as HTMLElement).dataset.enhanced = 'true'
  })
  mermaid.initialize({ startOnLoad: false })
  await mermaid.run({ nodes: Array.from(container.querySelectorAll('.mermaid')) })
}

async function renderMath(container: HTMLElement) {
  const renderMathInElement = await lazyKatexAuto()
  if (typeof renderMathInElement !== 'function') {
    return
  }
  renderMathInElement(container, {
    delimiters: [
      { left: '$$', right: '$$', display: true },
      { left: '$', right: '$', display: false },
      { left: '\\(', right: '\\)', display: false },
      { left: '\\[', right: '\\]', display: true },
    ],
    throwOnError: false,
    strict: false,
    ignoredTags: ['script', 'noscript', 'style', 'textarea', 'pre', 'code'],
    ignoredClasses: ['mermaid', 'markmap'],
  })
}

export async function enhanceMarkdown(container: HTMLElement, options: EnhanceOptions = {}) {
  if (!container) return
  rewriteImageSources(container, options.imagesBaseUrl)
  const renderMarkmapEnabled = options.renderMarkmap !== false
  const renderMermaidEnabled = options.renderMermaid !== false
  const renderMathEnabled = options.renderMath !== false
  if (renderMarkmapEnabled) {
    deferHeavyRender(container, 'code.language-markmap', () => renderMarkmap(container))
  }
  if (renderMermaidEnabled) {
    deferHeavyRender(container, 'code.language-mermaid', () => renderMermaid(container))
  }
  if (renderMathEnabled) {
    await renderMath(container)
  }
  initFootnoteTips(container)
}

function deferHeavyRender(container: HTMLElement, selector: string, render: () => void) {
  const blocks = Array.from(container.querySelectorAll(selector))
  if (!blocks.length) return
  if (!('IntersectionObserver' in window)) {
    if ('requestIdleCallback' in window) {
      ;(window as unknown as { requestIdleCallback: (cb: () => void) => void }).requestIdleCallback(render)
    } else {
      setTimeout(render, 16)
    }
    return
  }
  const observer = new IntersectionObserver(
    (entries) => {
      const visible = entries.some((entry) => entry.isIntersecting)
      if (!visible) return
      observer.disconnect()
      render()
    },
    { root: null, rootMargin: '200px' }
  )
  observer.observe(container)
}
