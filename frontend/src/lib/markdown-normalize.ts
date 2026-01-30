export function normalizeMarkdown(source: string) {
  let normalized = source || ''
  normalized = normalized.replace(/\\operatorname(?!\s*\{)\s*([A-Za-z]+)/g, '\\\\operatorname{$1}')
  normalized = normalized.replace(/<tex-math[^>]*>([\s\S]*?)<\/tex-math>/gi, (_match, tex) => `$${tex}$`)
  normalized = normalized.replace(/<inline-formula[^>]*>([\s\S]*?)<\/inline-formula>/gi, (_match, inner) => {
    const texMatch = /<tex-math[^>]*>([\s\S]*?)<\/tex-math>/i.exec(inner)
    if (texMatch && texMatch[1]) {
      return `$${texMatch[1]}$`
    }
    return inner
  })
  return normalized
}
