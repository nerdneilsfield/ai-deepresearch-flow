function unwrapDefault<T>(mod: unknown): T {
  const first = (mod as { default?: unknown } | null)?.default ?? mod
  return ((first as { default?: unknown } | null)?.default ?? first) as T
}

export async function lazySnippet() {
  const mod = await import('./snippet')
  return mod.renderSnippet
}

export async function lazyZip() {
  const mod = await import('jszip')
  return unwrapDefault<typeof import('jszip')>(mod)
}

export async function lazySaveAs() {
  const mod = await import('file-saver')
  const unwrapped = unwrapDefault<((data: Blob | string, filename?: string) => void) & { saveAs?: (data: Blob | string, filename?: string) => void }>(mod)
  return unwrapped.saveAs ?? unwrapped
}

export async function lazyMermaid() {
  const mod = await import('mermaid')
  return unwrapDefault<typeof import('mermaid').default>(mod)
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
  return unwrapDefault<(element: HTMLElement, options?: unknown) => void>(mod)
}
