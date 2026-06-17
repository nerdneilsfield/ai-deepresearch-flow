export type MermaidTheme = 'light' | 'dark'

export type MermaidRenderer = {
  initialize: (config: Record<string, unknown>) => void
  render: (
    id: string,
    source: string,
  ) => Promise<string | { svg?: string; bindFunctions?: (element: Element) => void }> | string | { svg?: string; bindFunctions?: (element: Element) => void }
}

export type RenderMermaidCodeBlocksOptions = {
  idPrefix: string
  theme: MermaidTheme
  renderer: MermaidRenderer
  sanitizeSvg: (svg: string) => string | Promise<string>
  onError?: (error: unknown, source: string) => void
}

export type RenderMermaidCodeBlocksResult = {
  rendered: number
  failed: number
  skipped: number
}

function mermaidThemeVariables(theme: MermaidTheme) {
  if (theme === 'dark') {
    return {
      background: '#0f172a',
      mainBkg: '#1e293b',
      primaryColor: '#1e293b',
      primaryTextColor: '#e5e7eb',
      primaryBorderColor: '#64748b',
      secondaryColor: '#111827',
      secondaryTextColor: '#e5e7eb',
      tertiaryColor: '#0f172a',
      lineColor: '#94a3b8',
      textColor: '#e5e7eb',
      nodeTextColor: '#e5e7eb',
      edgeLabelBackground: '#0f172a',
      clusterBkg: '#111827',
      clusterBorder: '#475569',
    }
  }
  return {
    background: '#ffffff',
    mainBkg: '#ffffff',
    primaryColor: '#ffffff',
    primaryTextColor: '#1f2937',
    primaryBorderColor: '#94a3b8',
    secondaryColor: '#f8fafc',
    tertiaryColor: '#f8fafc',
    lineColor: '#475569',
    textColor: '#1f2937',
    nodeTextColor: '#1f2937',
    edgeLabelBackground: '#ffffff',
    clusterBkg: '#f8fafc',
    clusterBorder: '#cbd5e1',
  }
}

export function mermaidRenderConfig(theme: MermaidTheme) {
  return {
    startOnLoad: false,
    securityLevel: 'strict',
    theme: 'base',
    htmlLabels: false,
    flowchart: {
      htmlLabels: false,
      useMaxWidth: true,
    },
    themeVariables: mermaidThemeVariables(theme),
  }
}

function mermaidCodeBlocks(container: HTMLElement) {
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'pre code.language-mermaid, pre code.lang-mermaid, .md-editor-code pre code.language-mermaid, .md-editor-code pre code.lang-mermaid',
    ),
  )
}

function renderedSvg(output: Awaited<ReturnType<MermaidRenderer['render']>>) {
  if (typeof output === 'string') return output
  return output?.svg || ''
}

function replaceHostForCodeBlock(code: HTMLElement, replacement: HTMLElement) {
  const host = code.closest('.md-editor-code') || code.closest('pre')
  if (!host || !host.parentElement) return false
  host.replaceWith(replacement)
  return true
}

function normalizeMermaidSvgSize(wrapper: HTMLElement) {
  const svg = wrapper.querySelector<SVGSVGElement>('svg')
  if (!svg) return
  const viewBox = svg.getAttribute('viewBox') || ''
  const [, widthRaw] = viewBox.match(/^\s*[-+]?\d*\.?\d+(?:e[-+]?\d+)?\s+[-+]?\d*\.?\d+(?:e[-+]?\d+)?\s+([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s+([-+]?\d*\.?\d+(?:e[-+]?\d+)?)\s*$/i) || []
  const naturalWidth = Number(widthRaw)
  if (Number.isFinite(naturalWidth) && naturalWidth > 0) {
    svg.style.width = `${Math.ceil(naturalWidth)}px`
  }
  svg.style.maxWidth = '100%'
  svg.style.height = 'auto'
  svg.style.display = 'block'
  svg.style.marginInline = 'auto'
}

export async function renderMermaidCodeBlocks(
  container: HTMLElement,
  options: RenderMermaidCodeBlocksOptions,
): Promise<RenderMermaidCodeBlocksResult> {
  const result: RenderMermaidCodeBlocksResult = { rendered: 0, failed: 0, skipped: 0 }
  const blocks = mermaidCodeBlocks(container)
  if (!blocks.length) return result

  options.renderer.initialize(mermaidRenderConfig(options.theme))

  for (const [index, code] of blocks.entries()) {
    const source = code.textContent || ''
    if (!source.trim()) {
      result.skipped += 1
      continue
    }

    const wrapper = document.createElement('div')
    wrapper.className = 'md-editor-mermaid'
    wrapper.dataset.content = source
    wrapper.dataset.mermaidTheme = options.theme
    wrapper.textContent = source

    if (!replaceHostForCodeBlock(code, wrapper)) {
      result.skipped += 1
      continue
    }

    try {
      const output = await options.renderer.render(`${options.idPrefix}-${index}`, source)
      const svg = await options.sanitizeSvg(renderedSvg(output))
      if (!svg) throw new Error('Mermaid renderer returned an empty SVG')
      wrapper.innerHTML = svg
      normalizeMermaidSvgSize(wrapper)
      wrapper.setAttribute('data-processed', '')
      if (typeof output !== 'string') output.bindFunctions?.(wrapper)
      result.rendered += 1
    } catch (error) {
      wrapper.textContent = source
      options.onError?.(error, source)
      result.failed += 1
    }
  }

  return result
}
