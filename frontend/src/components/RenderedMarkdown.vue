<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onErrorCaptured, ref, watch } from 'vue'
import { MdPreview, config } from 'md-editor-v3'
import 'md-editor-v3/lib/preview.css'
import mermaid from 'mermaid'
import katex from 'katex'
import DOMPurify from 'dompurify'
import footnote from 'markdown-it-footnote'
import taskLists from 'markdown-it-task-lists'
import { normalizeMarkdown } from '@/lib/markdown-normalize'
import type { HeadList } from 'md-editor-v3'
import type { OutlineItem } from '@/lib/outline'
import { STATIC_BASE } from '@/lib/config'
import { useTheme } from '@/composables/useTheme'
import { resolveMarkdownItPlugin } from '@/lib/module-interop'

// Global configuration for md-editor-v3
mermaid.initialize({ startOnLoad: false })
config({
  editorExtensions: {
    mermaid: {
      instance: mermaid,
      enableZoom: true,
    },
    katex: {
      instance: katex,
    },
  },
  katexConfig(baseConfig) {
    return { ...baseConfig, throwOnError: false, strict: false }
  },
  markdownItConfig(md) {
    md.use(resolveMarkdownItPlugin(footnote))
    md.use(resolveMarkdownItPlugin(taskLists))
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

const { themeMode } = useTheme()
const MAX_RICH_MARKDOWN_CHARS = 500_000
const editorTheme = computed(() => {
  if (themeMode.value === 'dark') return 'dark'
  if (themeMode.value === 'light') return 'light'
  // system: check actual applied theme from document
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light'
})

const editorId = `md-preview-${Math.random().toString(36).slice(2, 9)}`
type RendererDiagnosticKind = 'math' | 'mermaid' | 'renderer_exception' | 'sanitizer'
type RendererDiagnosticSeverity = 'warning' | 'error'
type RendererDiagnostic = {
  kind: RendererDiagnosticKind
  severity: RendererDiagnosticSeverity
  title: string
  message: string
  excerpt: string
  details: string
}

const rendererDiagnostics = ref<RendererDiagnostic[]>([])
const effectiveImagesBase = computed(() => props.imagesBaseUrl || STATIC_BASE || '')
const isOversizedMarkdown = computed(() => props.markdown.length > MAX_RICH_MARKDOWN_CHARS)
const isPlainFallbackTruncated = computed(() => props.markdown.length > MAX_RICH_MARKDOWN_CHARS)
const plainFallbackMarkdown = computed(() => props.markdown.slice(0, MAX_RICH_MARKDOWN_CHARS))
const safeUriPattern = /^(?:(?:https?|mailto):|data:image\/|blob:|\/|#|\.{1,2}\/|[a-z0-9+.-]+(?:[/?#]|$)|[^a-z])/i
const forbiddenHtmlAttrs = [
  'style',
  'onerror',
  'onload',
  'onclick',
  'onmouseover',
  'onfocus',
  'onmouseenter',
  'onmouseleave',
]
const rendererHtmlAttrs = [
  'target',
  'rel',
  'class',
  'aria-hidden',
  'aria-label',
  'data-processed',
  'data-content',
  'data-line',
  'data-closed',
  'data-mermaid-theme',
]

function truncateDiagnosticText(value: string, limit = 800) {
  const normalized = String(value || '').replace(/\s+/g, ' ').trim()
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, limit)}…`
}

function errorMessage(err: unknown) {
  if (err instanceof Error) return `${err.name}: ${err.message}`
  return String(err)
}

function replaceDiagnostic(nextDiagnostic: RendererDiagnostic) {
  rendererDiagnostics.value = [
    ...rendererDiagnostics.value.filter((item) => item.kind !== nextDiagnostic.kind),
    nextDiagnostic,
  ]
}

function extractMermaidBlocks(md: string) {
  const blocks: string[] = []
  md.replace(/```mermaid[^\n]*\n([\s\S]*?)```/gi, (_match, content: string) => {
    blocks.push(content.trim())
    return _match
  })
  return blocks
}

function extractMathSnippets(md: string) {
  const snippets: string[] = []
  md.replace(/\$\$([\s\S]+?)\$\$/g, (_match, content: string) => {
    snippets.push(content.trim())
    return _match
  })
  md.replace(/\\\[([\s\S]+?)\\\]/g, (_match, content: string) => {
    snippets.push(content.trim())
    return _match
  })
  md.replace(/\\\(([\s\S]+?)\\\)/g, (_match, content: string) => {
    snippets.push(content.trim())
    return _match
  })
  md.replace(/(^|[^\\$])\$([^$\n]+?)\$/g, (_match, _prefix: string, content: string) => {
    snippets.push(content.trim())
    return _match
  })
  return snippets.filter(Boolean)
}

function rendererDomDetails(root: HTMLElement) {
  const mathRendered = root.querySelectorAll('.md-editor-katex-inline[data-processed], .md-editor-katex-block[data-processed]').length
  const mathUnprocessed = root.querySelectorAll('.md-editor-katex-inline:not([data-processed]), .md-editor-katex-block:not([data-processed])').length
  const katexNodes = root.querySelectorAll('.katex').length
  const mermaidNodes = root.querySelectorAll('.md-editor-mermaid').length
  const mermaidProcessed = root.querySelectorAll('.md-editor-mermaid[data-processed]').length
  const mermaidSvg = root.querySelectorAll('.md-editor-mermaid[data-processed] svg').length
  return [
    `mathRendered=${mathRendered}`,
    `mathUnprocessed=${mathUnprocessed}`,
    `katexNodes=${katexNodes}`,
    `mermaidNodes=${mermaidNodes}`,
    `mermaidProcessed=${mermaidProcessed}`,
    `mermaidSvg=${mermaidSvg}`,
    `textExcerpt=${truncateDiagnosticText(root.textContent || '', 500)}`,
  ].join('\n')
}

function auditRendererOutput(
  root: HTMLElement,
  currentMd: string,
  options: { includePendingMermaid?: boolean } = {},
) {
  const diagnostics: RendererDiagnostic[] = []
  const mathSnippets = extractMathSnippets(currentMd)
  const mermaidBlocks = extractMermaidBlocks(currentMd)
  const hasRenderedMath = root.querySelector('.md-editor-katex-inline[data-processed], .md-editor-katex-block[data-processed]')
  const unprocessedMermaid = Array.from(root.querySelectorAll<HTMLElement>('.md-editor-mermaid')).filter(
    (node) => !node.hasAttribute('data-processed') || !node.querySelector('svg'),
  )

  if (mathSnippets.length > 0 && !hasRenderedMath) {
    diagnostics.push({
      kind: 'math',
      severity: 'error',
      title: 'Math source was detected but no rendered KaTeX output was found.',
      message: 'The markdown contains formula delimiters after normalization, but the preview DOM has no KaTeX output. The formula may have been dropped by the renderer, sanitizer, or extension setup.',
      excerpt: truncateDiagnosticText(mathSnippets.slice(0, 3).join('\n\n')),
      details: rendererDomDetails(root),
    })
  }

  if (options.includePendingMermaid && mermaidBlocks.length > 0 && unprocessedMermaid.length > 0) {
    diagnostics.push({
      kind: 'mermaid',
      severity: 'warning',
      title: 'Mermaid source is visible but was not rendered to SVG.',
      message: 'The markdown contains Mermaid fences, but at least one Mermaid preview node stayed unprocessed. Showing the original diagram source so the content is not silently lost.',
      excerpt: truncateDiagnosticText(mermaidBlocks.slice(0, 3).join('\n\n')),
      details: rendererDomDetails(root),
    })
  }

  const persistentDiagnostics = rendererDiagnostics.value.filter(
    (item) => item.kind !== 'math' && item.kind !== 'mermaid',
  )
  rendererDiagnostics.value = [...persistentDiagnostics, ...diagnostics]
}

function sanitizeHtml(html: string) {
  return DOMPurify.sanitize(String(html || ''), {
    ADD_ATTR: rendererHtmlAttrs,
    FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed'],
    FORBID_ATTR: forbiddenHtmlAttrs,
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: safeUriPattern,
  })
}

async function sanitizeMermaidSvg(svg: string) {
  try {
    return DOMPurify.sanitize(String(svg || ''), {
      USE_PROFILES: { svg: true, svgFilters: true },
      FORBID_TAGS: ['script', 'foreignObject'],
      FORBID_ATTR: forbiddenHtmlAttrs,
      ALLOW_DATA_ATTR: false,
      ALLOWED_URI_REGEXP: safeUriPattern,
    })
  } catch (err) {
    replaceDiagnostic({
      kind: 'sanitizer',
      severity: 'error',
      title: 'Mermaid SVG sanitizer failed.',
      message: 'The rendered Mermaid SVG could not be sanitized safely, so it was not displayed.',
      excerpt: truncateDiagnosticText(String(svg || '')),
      details: errorMessage(err),
    })
    return ''
  }
}

function sanitizeMarkmapNodeContent(html: string) {
  return DOMPurify.sanitize(String(html || ''), {
    ALLOWED_TAGS: ['a', 'br', 'code', 'em', 'span', 'strong', 'sub', 'sup'],
    ALLOWED_ATTR: ['class', 'href', 'rel', 'target', 'title'],
    ALLOW_DATA_ATTR: false,
    ALLOWED_URI_REGEXP: safeUriPattern,
  })
}

function sanitizeMarkmapTree(node: any) {
  if (!node || typeof node !== 'object') return
  if (typeof node.content === 'string') {
    node.content = sanitizeMarkmapNodeContent(node.content)
  }
  if (Array.isArray(node.children)) {
    node.children.forEach(sanitizeMarkmapTree)
  }
}

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
let transformTimer: ReturnType<typeof setTimeout> | null = null
let diagnosticTimer: ReturnType<typeof setTimeout> | null = null
let lastTransformedMarkdown = ''

watch(
  isOversizedMarkdown,
  (oversized) => {
    if (transformTimer) {
      clearTimeout(transformTimer)
      transformTimer = null
    }
    if (diagnosticTimer) {
      clearTimeout(diagnosticTimer)
      diagnosticTimer = null
    }
    lastTransformedMarkdown = ''
    if (oversized) emit('outline', [])
  },
  { immediate: true },
)

watch(
  () => props.markdown,
  () => {
    rendererDiagnostics.value = []
    lastTransformedMarkdown = ''
    if (diagnosticTimer) {
      clearTimeout(diagnosticTimer)
      diagnosticTimer = null
    }
  },
)

onErrorCaptured((err, _instance, info) => {
  replaceDiagnostic({
    kind: 'renderer_exception',
    severity: 'error',
    title: 'Markdown renderer threw an exception.',
    message: `Vue captured an exception while rendering markdown (${info}). The raw markdown was kept available in this diagnostic panel.`,
    excerpt: truncateDiagnosticText(processedMarkdown.value),
    details: errorMessage(err),
  })
  return false
})

async function handleHtmlChanged() {
  if (isOversizedMarkdown.value) return
  // Skip if the markdown content hasn't actually changed (e.g., resize-triggered events)
  const currentMd = processedMarkdown.value
  if (currentMd === lastTransformedMarkdown) return
  lastTransformedMarkdown = currentMd

  if (transformTimer) clearTimeout(transformTimer)

  transformTimer = setTimeout(async () => {
    await nextTick()
    const root = document.getElementById(editorId)
    if (!root) return
    auditRendererOutput(root, currentMd)
    if (diagnosticTimer) clearTimeout(diagnosticTimer)
    diagnosticTimer = setTimeout(() => {
      const latestRoot = document.getElementById(editorId)
      if (!latestRoot || processedMarkdown.value !== currentMd) return
      auditRendererOutput(latestRoot, currentMd, { includePendingMermaid: true })
    }, 700)

    // 1. Footnote Hover
    const refs = root.querySelectorAll('sup.footnote-ref a:not([title])')
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

    // 2. Markmap Rendering — only process new (unrendered) divs
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

        try {
          const { root: tree } = transformer.transform(source)
          sanitizeMarkmapTree(tree)
          const mmInstance = Markmap.create(svg, undefined, tree)
          div.replaceWith(wrapper)
          setTimeout(() => {
            ;(mmInstance as { fit?: () => void })?.fit?.()
          }, 200)
        } catch (err) {
          div.replaceWith(wrapper)
          wrapper.textContent = 'Failed to render markmap.'
        }
      })
    }
  }, 150)
}

onBeforeUnmount(() => {
  if (transformTimer) clearTimeout(transformTimer)
  if (diagnosticTimer) clearTimeout(diagnosticTimer)
})
</script>

<template>
  <div class="md-preview-wrapper prose prose-slate max-w-none prose-headings:text-ink-900 prose-p:text-ink-700 prose-a:text-blue-600 prose-blockquote:border-l-4 prose-blockquote:border-accent-500 prose-blockquote:bg-accent-50 prose-blockquote:py-1 prose-blockquote:px-4 prose-code:text-accent-700 prose-pre:bg-ink-900 prose-pre:text-ink-50 prose-img:rounded-lg prose-img:shadow-md dark:prose-invert" :class="props.class">
    <div
      v-if="isOversizedMarkdown"
      role="note"
      class="rounded-lg border border-amber-200 bg-amber-50 p-4 text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <p class="mb-3 font-medium">
        Markdown is too large for rich rendering; showing a plain-text preview.
      </p>
      <p v-if="isPlainFallbackTruncated" class="mb-3 text-sm opacity-80">
        The plain-text preview was truncated to keep the browser responsive.
      </p>
      <pre class="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-md bg-white/70 p-3 text-sm text-ink-800 dark:bg-ink-950/70 dark:text-ink-100">{{ plainFallbackMarkdown }}</pre>
    </div>
    <MdPreview
      v-else
      :editorId="editorId"
      :modelValue="processedMarkdown"
      :noMermaid="false"
      :noEcharts="true"
      :sanitize="sanitizeHtml"
      :sanitizeMermaid="sanitizeMermaidSvg"
      :theme="editorTheme"
      @onGetCatalog="handleCatalog"
      @onHtmlChanged="handleHtmlChanged"
      class="bg-transparent [&_.md-editor]:bg-transparent"
    />
    <div
      v-if="rendererDiagnostics.length"
      data-testid="markdown-renderer-diagnostics"
      class="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-100"
    >
      <p class="font-semibold">Markdown renderer diagnostics</p>
      <p class="mt-1 text-xs opacity-80">
        The source is still available below so formula or diagram content is not silently lost.
      </p>
      <ul class="mt-3 space-y-3">
        <li
          v-for="diagnostic in rendererDiagnostics"
          :key="diagnostic.kind"
          class="rounded-md border border-amber-200 bg-white/70 p-3 dark:border-amber-800 dark:bg-ink-950/50"
          :data-renderer-diagnostic-kind="diagnostic.kind"
          :data-renderer-diagnostic-severity="diagnostic.severity"
        >
          <div class="flex flex-wrap items-center gap-2">
            <span class="rounded bg-amber-100 px-2 py-0.5 text-xs font-semibold uppercase tracking-wide dark:bg-amber-900">
              {{ diagnostic.severity }}
            </span>
            <strong>{{ diagnostic.title }}</strong>
          </div>
          <p class="mt-2">{{ diagnostic.message }}</p>
          <details class="mt-2">
            <summary class="cursor-pointer font-medium">Source excerpt</summary>
            <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-xs text-ink-800 dark:bg-ink-950/80 dark:text-ink-100">{{ diagnostic.excerpt }}</pre>
          </details>
          <details class="mt-2">
            <summary class="cursor-pointer font-medium">Renderer details</summary>
            <pre class="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-white/80 p-2 text-xs text-ink-800 dark:bg-ink-950/80 dark:text-ink-100">{{ diagnostic.details }}</pre>
          </details>
        </li>
      </ul>
    </div>
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
