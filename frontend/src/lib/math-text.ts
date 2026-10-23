import katex from 'katex'

// MinerU/BibTeX titles often have "$x$ -PCR"; the space after the formula is an extraction artifact.
const fixSpacing = (text: string) => text.replace(/\$ +-(?=\w)/g, '$-')

const escapeHtml = (text: string) =>
  text.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!)

/** Render plain text with inline `$...$` TeX as safe HTML (everything outside formulas is escaped). */
export function renderMathText(text: string | null | undefined): string {
  return fixSpacing(String(text || ''))
    .split(/(\$[^$\n]+\$)/)
    .map((part) =>
      /^\$[^$\n]+\$$/.test(part)
        ? katex.renderToString(part.slice(1, -1), { throwOnError: false, strict: false })
        : escapeHtml(part),
    )
    .join('')
}

/** Strip inline TeX markup for places that only accept plain text (document.title, tooltips). */
export function plainMathText(text: string | null | undefined): string {
  return fixSpacing(String(text || ''))
    .replace(/\$([^$\n]+)\$/g, (_m, tex: string) => tex.replace(/\\[a-zA-Z]+\s*/g, '').replace(/[{}]/g, ''))
}
