declare module 'file-saver' {
  export function saveAs(data: Blob | string, filename?: string, options?: { autoBom?: boolean }): void
}

declare module 'katex/contrib/auto-render' {
  import type { KatexOptions } from 'katex'
  
  export interface RenderOptions {
    delimiters?: Array<{ left: string; right: string; display: boolean }>
    ignoredTags?: string[]
    ignoredClasses?: string[]
    throwOnError?: boolean
    strict?: boolean
  }
  
  export function renderMathInElement(element: HTMLElement, options?: RenderOptions): void
}
