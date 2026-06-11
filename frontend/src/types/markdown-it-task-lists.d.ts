declare module 'markdown-it-task-lists' {
  import type MarkdownIt from 'markdown-it'

  export interface MarkdownItTaskListsOptions {
    enabled?: boolean
    label?: boolean
    labelAfter?: boolean
  }

  export default function taskLists(md: MarkdownIt, options?: MarkdownItTaskListsOptions): void
}
