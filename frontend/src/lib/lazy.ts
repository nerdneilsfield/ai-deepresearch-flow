import { resolveCallableExport, resolveDefaultExport } from './module-interop'

export async function lazySnippet() {
  const mod = await import('./snippet')
  return mod.renderSnippet
}

export async function lazyZip() {
  const mod = await import('jszip')
  return resolveDefaultExport<typeof import('jszip')>(mod)
}

export async function lazySaveAs() {
  const mod = await import('file-saver')
  const candidate = resolveDefaultExport<((data: Blob | string, filename?: string) => void) & { saveAs?: (data: Blob | string, filename?: string) => void }>(mod)
  return candidate.saveAs ?? candidate
}

export async function lazyMermaid() {
  const mod = await import('mermaid')
  return resolveDefaultExport<typeof import('mermaid').default>(mod)
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
  return resolveCallableExport<(element: HTMLElement, options?: unknown) => void>(mod)
}
