import type MarkdownIt from 'markdown-it'

export function resolveDefaultExport<T>(mod: unknown): T {
  const first = (mod as { default?: unknown } | null)?.default ?? mod
  return first as T
}

export function resolveCallableExport<T extends (...args: any[]) => any>(mod: unknown): T {
  if (typeof mod === 'function') return mod as T
  const first = (mod as { default?: unknown } | null)?.default
  if (typeof first === 'function') return first as T
  const nested = (first as { default?: unknown } | null)?.default
  if (typeof nested === 'function') return nested as T
  throw new TypeError('Module export is not callable')
}

export function resolveMarkdownItPlugin(plugin: unknown): (md: MarkdownIt, ...args: unknown[]) => void {
  try {
    return resolveCallableExport<(md: MarkdownIt, ...args: unknown[]) => void>(plugin)
  } catch {
    throw new TypeError('Markdown-it plugin export is not callable')
  }
}
