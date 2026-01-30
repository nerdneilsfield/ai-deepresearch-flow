<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount } from 'vue'
import { MdPreview, config } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import footnote from 'markdown-it-footnote'
// @ts-ignore
import taskLists from 'markdown-it-task-lists'
import { normalizeMarkdown } from '@/lib/markdown-normalize'
import type { HeadList } from 'md-editor-v3'
import type { OutlineItem } from '@/lib/outline'
import { STATIC_BASE } from '@/lib/config'

// Global configuration for md-editor-v3
config({
  markdownItConfig(md) {
    md.use(footnote)
    md.use(taskLists)
  }
})

const props = defineProps<{
  markdown: string
  imagesBaseUrl?: string | null
  class?: string
}>()

const emit = defineEmits<{
  (event: 'outline', outline: OutlineItem[]): void
}>()

const editorId = `md-preview-${Math.random().toString(36).slice(2, 9)}`
const effectiveImagesBase = computed(() => props.imagesBaseUrl || STATIC_BASE || '')

// Image URL rewriting
const processedMarkdown = computed(() => {
  let md = normalizeMarkdown(props.markdown)
  
  const rawBase = effectiveImagesBase.value
  const baseUrl = rawBase.replace(/\/+$/, '')

  // 0. Heuristic: Wrap naked Mermaid diagrams (State Machine approach)
  const lines = md.split('\n')
  const newLines: string[] = []
  let inFence = false
  let inMermaidAuto = false

  const mermaidStart = /^\s*(graph [A-Z]{2}|sequenceDiagram|classDiagram|stateDiagram|stateDiagram-v2|gantt|pie|gitGraph|erDiagram|journey|requirementDiagram|c4Context)/

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (typeof line !== 'string') continue
    
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      inFence = !inFence
      if (inMermaidAuto) {
        newLines.push('```')
        inMermaidAuto = false
      }
      newLines.push(line)
      continue
    }

    if (!inFence && !inMermaidAuto) {
      if (mermaidStart.test(line)) {
        inMermaidAuto = true
        newLines.push('```mermaid')
        newLines.push(line)
        continue
      }
    }

    if (inMermaidAuto) {
      if (trimmed === '') {
        newLines.push('```')
        newLines.push(line)
        inMermaidAuto = false
        continue
      }
    }

    newLines.push(line)
  }

  if (inMermaidAuto) {
    newLines.push('```')
  }

  md = newLines.join('\n')

  // 1. Fix Mermaid blocks if they contain <br> or HTML entities
  md = md.replace(/```mermaid\s*([\s\S]*?)```/g, (_match: string, content: string) => {
    let cleanContent = content.replace(/<br\s*\/?>/gi, '\n')
    cleanContent = cleanContent
      .replace(/&gt;/g, '>')
      .replace(/&lt;/g, '<')
      .replace(/&amp;/g, '&')
      .replace(/&quot;/g, '"')
    return '```mermaid\n' + cleanContent + '\n```'
  })

  // 2. Rewrite Image URLs
  if (baseUrl) {
    const isImagesPath = baseUrl.endsWith('/images')
    
    // Markdown Image: ![alt](src)
    md = md.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (match: string, alt: string, src: string) => {
      if (!src || /^(https?:|data:|blob:)/i.test(src)) return match
      let cleaned = src.replace(/^\.?\//, '')
      if (cleaned.startsWith('paper/images/')) cleaned = cleaned.replace(/^paper\//, '')
      if (isImagesPath && cleaned.startsWith('images/')) cleaned = cleaned.slice(7)
      return `![${alt}](${baseUrl}/${cleaned})`
    })
    
    // HTML Image: <img src="...">
    md = md.replace(
      /<img\s+([^>]*?)src=["']([^"']+)["']([^>]*?)>/gi,
      (match: string, p1: string, src: string, p2: string) => {
       if (!src || /^(https?:|data:|blob:)/i.test(src)) return match
       let cleaned = src.replace(/^\.?\//, '')
       if (cleaned.startsWith('paper/images/')) cleaned = cleaned.replace(/^paper\//, '')
       if (isImagesPath && cleaned.startsWith('images/')) cleaned = cleaned.slice(7)
        return `<img ${p1}src="${baseUrl}/${cleaned}"${p2}>`
      }
    )
  }

  // 3. Convert markmap
  md = md.replace(/```markmap\s*\n([\s\S]*?)\n```/g, (_match: string, content: string) => {
    const escaped = content
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
    return '<div class="paperdb-markmap-raw">' + escaped + '</div>'
  })

  return md
})

function handleCatalog(list: HeadList[]) {
  const items: OutlineItem[] = list.map(item => ({
    text: item.text,
    level: item.level,
    id: item.text
  }))
  emit('outline', items)
}

let markmapDepsPromise: Promise<any> | null = null
let transformTimer: any = null

async function handleHtmlChanged() {
  if (transformTimer) clearTimeout(transformTimer)
  
  transformTimer = setTimeout(async () => {
    await nextTick()
    const root = document.getElementById(editorId)
    if (!root) return

    // 1. Footnote Hover
    const refs = root.querySelectorAll('sup.footnote-ref a')
    refs.forEach((ref) => {
      const href = ref.getAttribute('href')
      if (!href) return
      const targetId = href.startsWith('#') ? href.slice(1) : href
      const target = document.getElementById(targetId)
      if (target) {
        let text = target.textContent?.trim() || ''
        text = text.replace(/[↩]/g, '').trim()
        ref.setAttribute('title', text)
      }
    })

    // 2. Markmap Rendering
    const markmapDivs = root.querySelectorAll('.paperdb-markmap-raw')
    if (markmapDivs.length > 0) {
      if (!markmapDepsPromise) {
        markmapDepsPromise = Promise.all([
          import('markmap-lib'),
          import('markmap-view'),
        ])
      }
      
      const [{ Transformer }, { Markmap }] = await markmapDepsPromise
      const transformer = new Transformer()

      markmapDivs.forEach((div) => {
        const source = div.textContent || ''
        const wrapper = document.createElement('div')
        wrapper.className = 'markmap-container w-full h-[500px] my-4 border border-ink-100 rounded-lg overflow-hidden shadow-sm'
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
        svg.setAttribute('class', 'w-full h-full')
        wrapper.appendChild(svg)
        
        div.replaceWith(wrapper)
        
        try {
          const { root: tree } = transformer.transform(source)
          const mmInstance = Markmap.create(svg, undefined, tree)
          setTimeout(() => {
            // @ts-ignore
            mmInstance?.fit()
          }, 200)
        } catch (err) {
          wrapper.textContent = 'Failed to render markmap.'
        }
      })
    }
  }, 100)
}

onBeforeUnmount(() => {
  if (transformTimer) clearTimeout(transformTimer)
})
</script>

<template>
  <div class="md-preview-wrapper prose prose-slate max-w-none prose-headings:text-ink-900 prose-p:text-ink-700 prose-a:text-blue-600 prose-blockquote:border-l-4 prose-blockquote:border-accent-500 prose-blockquote:bg-accent-50 prose-blockquote:py-1 prose-blockquote:px-4 prose-code:text-accent-700 prose-pre:bg-ink-900 prose-pre:text-ink-50 prose-img:rounded-lg prose-img:shadow-md dark:prose-invert" :class="props.class">
    <MdPreview
      :editorId="editorId"
      :modelValue="processedMarkdown"
      :noMermaid="false"
      @onGetCatalog="handleCatalog"
      @onHtmlChanged="handleHtmlChanged"
      class="bg-transparent"
    />
  </div>
</template>

<style scoped>
:deep(.md-editor-preview-wrapper) {
  padding: 0;
}
:deep(.md-editor-preview) {
  color: inherit;
  font-family: inherit;
}
:deep(.katex-display) {
  overflow-x: auto;
  overflow-y: hidden;
}
/* Center images - force override */
:deep(.md-editor-preview img),
:deep(.prose img) {
  display: block !important;
  margin-left: auto !important;
  margin-right: auto !important;
  max-width: 100%;
  height: auto;
  border-radius: 0.5rem;
  box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
}
:deep(.md-editor-preview p:has(> img)),
:deep(.md-editor-preview figure) {
  text-align: center !important;
  display: flex !important;
  justify-content: center !important;
}
:deep(.paperdb-markmap-raw) {
  display: none;
}
</style>
