import { renderMarkdown } from './markdown'

export function renderSnippet(snippet: string) {
  const withMarks = (snippet || '')
    .replace(/\[\[\[/g, '<mark class="bg-amber-300/80 text-amber-950 font-semibold rounded px-0.5 dark:bg-amber-400/30 dark:text-amber-100">')
    .replace(/\]\]\]/g, '</mark>')
  return renderMarkdown(withMarks)
}
