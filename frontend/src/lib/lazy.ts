export async function lazySnippet() {
  const mod = await import('./snippet')
  return mod.renderSnippet
}

export async function lazyZip() {
  const mod = await import('jszip')
  return mod.default
}

export async function lazySaveAs() {
  const mod = await import('file-saver')
  return mod.saveAs
}

export async function lazyMermaid() {
  const mod = await import('mermaid')
  return mod.default
}

export async function lazyMarkmap() {
  const [{ Transformer }, { Markmap }] = await Promise.all([
    import('markmap-lib'),
    import('markmap-view'),
  ])
  return { Transformer, Markmap }
}

export async function lazyKatexAuto() {
  const mod = await import('katex/contrib/auto-render')
  const fn =
    (mod as unknown as { renderMathInElement?: (element: HTMLElement, options?: unknown) => void }).renderMathInElement ||
    (mod as unknown as { default?: (element: HTMLElement, options?: unknown) => void }).default
  return fn
}
