export type OutlineItem = {
  id: string
  text: string
  level: number
}

function slugify(text: string) {
  const base = text
    .trim()
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af-]+/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
  return base
}

export function prepareOutline(html: string) {
  if (!html) {
    return { html: '', outline: [] as OutlineItem[] }
  }
  const parser = new DOMParser()
  const doc = parser.parseFromString(html, 'text/html')
  const headings = Array.from(doc.body.querySelectorAll('h1, h2, h3, h4'))
  const seen = new Map<string, number>()
  const outline: OutlineItem[] = []

  headings.forEach((heading, index) => {
    const text = (heading.textContent || '').trim()
    if (!text) return
    let base = slugify(text)
    if (!base) base = `section-${index + 1}`
    const count = seen.get(base) || 0
    const id = count ? `${base}-${count + 1}` : base
    seen.set(base, count + 1)
    heading.id = id
    outline.push({ id, text, level: Number(heading.tagName.replace('H', '')) || 2 })
  })

  return { html: doc.body.innerHTML, outline }
}

export function buildOutlineFromDom(root: HTMLElement) {
  const headings = Array.from(root.querySelectorAll('h1, h2, h3, h4'))
  const seen = new Map<string, number>()
  const outline: OutlineItem[] = []

  headings.forEach((heading, index) => {
    const text = (heading.textContent || '').trim()
    if (!text) return
    let base = slugify(text)
    if (!base) base = `section-${index + 1}`
    const count = seen.get(base) || 0
    const id = count ? `${base}-${count + 1}` : base
    seen.set(base, count + 1)
    heading.id = id
    outline.push({ id, text, level: Number(heading.tagName.replace('H', '')) || 2 })
  })

  return outline
}
