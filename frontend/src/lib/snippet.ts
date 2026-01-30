import { renderMarkdown } from './markdown'

export function renderSnippet(snippet: string) {
  const withMarks = (snippet || '')
    .replace(/\[\[\[/g, '<mark class="bg-amber-200/70 text-amber-900 font-normal rounded-sm px-0.5">')
    .replace(/\]\]\]/g, '</mark>')
  return renderMarkdown(withMarks)
}
