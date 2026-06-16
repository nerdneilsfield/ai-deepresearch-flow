<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
import { MAX_BATCH_SIZE } from '@/lib/config'
import { getPaperDetail, matchBibtex } from '@/lib/api'
import type { BibtexMatchedItem, BibtexUnmatchedItem } from '@/lib/api'
import type { SearchItem } from '@/types/api'
import {
  discoverSummaryTemplates,
  downloadSelectedJsonl,
  downloadSelectedZip,
  selectedExportIssueCount,
  type SelectedDownloadMode,
  type SelectedDownloadOptions,
} from '@/lib/selected-export'
import { lazySaveAs, lazySnippet } from '@/lib/lazy'
import { useExpandableSummary } from '@/composables/useExpandableSummary'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import SearchResultItem from '@/components/search/SearchResultItem.vue'
import { Download, Upload, Save, Trash2, FileDown, FileUp } from 'lucide-vue-next'

const selection = useSelectionStore()
const ui = useUiStore()
const { t } = useI18n()
const downloading = ref(false)
const progress = ref(0)
const status = ref('')
const sizeBytes = ref(0)
const fileInput = ref<HTMLInputElement | null>(null)
const bibFileInput = ref<HTMLInputElement | null>(null)
const bibImporting = ref(false)
const bibProgress = ref(0)
const bibStatus = ref('')
const bibUnmatched = ref<BibtexUnmatchedItem[]>([])
const bibShowUnmatched = ref(true)
const bibMode = ref<'append' | 'replace'>('append')
const bibShowModePopover = ref(false)

const { expanded, expandedMarkdown, expandedLoading, toggleSummary } = useExpandableSummary()
const snippetRenderer = ref<(value: string) => string>((v) => v)

// Initialize snippet renderer
lazySnippet().then(fn => {
  // @ts-ignore
  snippetRenderer.value = (val: string) => String(fn(val))
})


const exportMode = ref<SelectedDownloadMode>('zip')
const includeMetadata = ref(false)
const includePdf = ref(true)
const includeSourceMarkdown = ref(true)
const includeTranslatedMarkdown = ref(true)
const includeImages = ref(true)
const includeSummaries = ref(true)
const availableSummaryTemplates = ref<string[]>([])
const selectedSummaryTemplates = ref<string[]>([])
const preferredSummaryTemplates = ref<string[]>([])
const summaryTemplatesLoading = ref(false)
const templateSelectionTouched = ref(false)
const zipAllSummaryTemplates = ref(true)
let templateDiscoveryRevision = 0

const selectedSnapshotKey = computed(() =>
  selection.items
    .map((item) => [item.paper_id, item.preferred_summary_template ?? '', item.summary_url ?? ''].join(':'))
    .join('|'),
)

const hasSelectedExportContent = computed(() => {
  if (exportMode.value === 'jsonl') {
    return includeMetadata.value || (includeSummaries.value && selectedSummaryTemplates.value.length > 0)
  }
  return (
    includeMetadata.value ||
    includePdf.value ||
    includeSourceMarkdown.value ||
    includeTranslatedMarkdown.value ||
    includeImages.value ||
    includeSummaries.value
  )
})

const summaryExportRequested = computed(() => includeSummaries.value && (selectedSummaryTemplates.value.length > 0 || (exportMode.value === 'zip' && zipAllSummaryTemplates.value)))

const canDownload = computed(
  () =>
    selection.count > 0 &&
    selection.count <= MAX_BATCH_SIZE &&
    hasSelectedExportContent.value &&
    !(summaryTemplatesLoading.value && summaryExportRequested.value),
)

const downloadButtonLabel = computed(() => {
  if (downloading.value) return t('preparing')
  return exportMode.value === 'jsonl' ? t('selectedExportDownloadJsonl') : t('downloadZip')
})

function setExportMode(mode: SelectedDownloadMode) {
  exportMode.value = mode
  if (mode === 'jsonl' && !includeMetadata.value && selectedSummaryTemplates.value.length === 0) {
    includeMetadata.value = true
  }
  if (mode === 'zip' && !templateSelectionTouched.value) {
    selectedSummaryTemplates.value = [...availableSummaryTemplates.value]
    zipAllSummaryTemplates.value = true
  }
}

function toggleSummaryTemplate(template: string) {
  templateSelectionTouched.value = true
  zipAllSummaryTemplates.value = false
  selectedSummaryTemplates.value = selectedSummaryTemplates.value.includes(template)
    ? selectedSummaryTemplates.value.filter((value) => value !== template)
    : [...selectedSummaryTemplates.value, template]
}

function applyDiscoveredTemplates(templates: string[], preferredTemplates: string[]) {
  availableSummaryTemplates.value = templates
  preferredSummaryTemplates.value = preferredTemplates
  if (templateSelectionTouched.value) {
    selectedSummaryTemplates.value = selectedSummaryTemplates.value.filter((template) => templates.includes(template))
    return
  }
  if (exportMode.value === 'zip') {
    selectedSummaryTemplates.value = [...templates]
    zipAllSummaryTemplates.value = true
    return
  }
  const defaults = preferredTemplates.length > 0 ? preferredTemplates : templates
  selectedSummaryTemplates.value = [...defaults]
}

watch(
  selectedSnapshotKey,
  async () => {
    const revision = ++templateDiscoveryRevision
    const items = [...selection.items]
    templateSelectionTouched.value = false
    zipAllSummaryTemplates.value = exportMode.value === 'zip'
    if (items.length === 0) {
      availableSummaryTemplates.value = []
      selectedSummaryTemplates.value = []
      preferredSummaryTemplates.value = []
      return
    }
    summaryTemplatesLoading.value = true
    try {
      const discovery = await discoverSummaryTemplates(items)
      if (revision !== templateDiscoveryRevision) return
      applyDiscoveredTemplates(discovery.templates, discovery.preferredTemplates)
    } catch (err) {
      console.warn('Failed to discover summary templates', err)
      if (revision === templateDiscoveryRevision) applyDiscoveredTemplates([], [])
    } finally {
      if (revision === templateDiscoveryRevision) summaryTemplatesLoading.value = false
    }
  },
  { immediate: true },
)

watch(exportMode, (mode) => {
  if (mode === 'zip' && !templateSelectionTouched.value) {
    selectedSummaryTemplates.value = [...availableSummaryTemplates.value]
    zipAllSummaryTemplates.value = true
  }
  if (mode === 'jsonl' && !templateSelectionTouched.value) {
    const defaults = preferredSummaryTemplates.value.length > 0 ? preferredSummaryTemplates.value : availableSummaryTemplates.value
    selectedSummaryTemplates.value = [...defaults]
    zipAllSummaryTemplates.value = false
  }
})


function translateExportStatus(value: string): string {
  if (value.startsWith('Building JSONL')) return t('selectedExportBuildingJsonl')
  if (value.startsWith('Fetching manifest')) return t('selectedExportFetchingManifest')
  if (value.startsWith('Compressing ZIP')) return t('selectedExportCompressingZip')
  return value
}

function currentDownloadOptions(): SelectedDownloadOptions {
  return {
    mode: exportMode.value,
    includeMetadata: includeMetadata.value,
    includePdf: exportMode.value === 'zip' && includePdf.value,
    includeSourceMarkdown: exportMode.value === 'zip' && includeSourceMarkdown.value,
    includeTranslatedMarkdown: exportMode.value === 'zip' && includeTranslatedMarkdown.value,
    includeImages: exportMode.value === 'zip' && includeImages.value,
    includeSummaries: includeSummaries.value,
    summaryTemplates: includeSummaries.value ? [...selectedSummaryTemplates.value] : [],
    includeAllManifestSummaryTemplates: includeSummaries.value && exportMode.value === 'zip' && zipAllSummaryTemplates.value,
  }
}

async function downloadAll() {
  if (!canDownload.value || downloading.value) return
  downloading.value = true
  progress.value = 0
  status.value = t('selectedExportPreparing')
  sizeBytes.value = 0

  try {
    const items = [...selection.items]
    const options = currentDownloadOptions()
    const result = options.mode === 'jsonl'
      ? await downloadSelectedJsonl(items, options, {
          onStatus: (value) => { status.value = translateExportStatus(value) },
          onProgress: (value) => { progress.value = value },
        })
      : await downloadSelectedZip(items, options, {
          onStatus: (value) => { status.value = translateExportStatus(value) },
          onProgress: (value) => { progress.value = value },
          onSizeBytes: (value) => { sizeBytes.value += value },
        })

    if (!result.saved) {
      ui.pushToast(t('selectedExportFailed'), 'error')
      return
    }

    status.value = t('selectedExportReady')
    const issueCount = selectedExportIssueCount(result.stats)
    if (issueCount > 0) {
      ui.pushToast(t('selectedExportCompletedWithMissing', { count: issueCount }), 'warning')
    } else {
      ui.pushToast(t('selectedExportCompleted'), 'success')
    }
  } catch (err) {
    console.error(err)
    ui.pushToast(t('selectedExportFailed'), 'error')
  } finally {
    downloading.value = false
  }
}

async function saveList() {
  const saveAs = await lazySaveAs()
  const slim = selection.items.map((item) => ({
    paper_id: item.paper_id,
    paper_index: item.paper_index,
    title: item.title,
  }))
  const data = JSON.stringify(slim, null, 2)
  const blob = new Blob([data], { type: 'application/json;charset=utf-8' })
  saveAs(blob, `paperdb_list_${Date.now()}.json`)
}

function triggerLoadList() {
  fileInput.value?.click()
}

function isFullItem(item: any): item is SearchItem {
  return item && typeof item.paper_id === 'string' && (item.title || item.venue || item.authors)
}

function toSearchItem(entry: any, detail?: any): SearchItem {
  if (detail) {
    return {
      paper_id: detail.paper_id,
      paper_index: entry.paper_index,
      title: detail.title || entry.title || '',
      year: detail.year ?? '',
      venue: detail.venue ?? '',
      authors: detail.authors ?? [],
      summary_preview: detail.summary_preview,
      snippet_markdown: detail.snippet_markdown,
      preferred_summary_template: detail.preferred_summary_template,
      has_pdf: !!detail.pdf_url,
      has_source: !!detail.source_md_url,
      has_translated: !!(detail.translated_md_urls && Object.keys(detail.translated_md_urls).length),
      pdf_url: detail.pdf_url ?? null,
      source_md_url: detail.source_md_url ?? null,
      translated_md_urls: detail.translated_md_urls ?? {},
      images_base_url: detail.images_base_url,
      summary_url: detail.summary_url,
      manifest_url: detail.manifest_url,
    }
  }

  return {
    paper_id: entry.paper_id,
    paper_index: entry.paper_index,
    title: entry.title || '',
    year: entry.year ?? '',
    venue: entry.venue ?? '',
    authors: entry.authors ?? [],
    summary_preview: entry.summary_preview,
    snippet_markdown: entry.snippet_markdown,
    preferred_summary_template: entry.preferred_summary_template,
    has_pdf: entry.has_pdf,
    has_source: entry.has_source,
    has_translated: entry.has_translated,
    pdf_url: entry.pdf_url ?? null,
    source_md_url: entry.source_md_url ?? null,
    translated_md_urls: entry.translated_md_urls ?? {},
    images_base_url: entry.images_base_url,
    summary_url: entry.summary_url,
    manifest_url: entry.manifest_url,
  }
}

function handleFileLoad(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  const reader = new FileReader()
  reader.onload = async (e) => {
    try {
      const content = e.target?.result as string
      const items = JSON.parse(content)
      if (Array.isArray(items)) {
        const validItems = items.filter((i: any) => i && typeof i.paper_id === 'string')
        selection.clear()
        let loaded = 0
        for (const entry of validItems) {
          if (isFullItem(entry)) {
            selection.add(toSearchItem(entry))
            loaded += 1
            continue
          }
          try {
            const detail = await getPaperDetail(entry.paper_id)
            selection.add(toSearchItem(entry, detail))
            loaded += 1
          } catch (err) {
            console.warn(`Failed to fetch detail for ${entry.paper_id}`)
          }
        }
        ui.pushToast(`Loaded ${loaded} papers`, 'success')
      } else {
        throw new Error('Invalid format')
      }
    } catch (err) {
      ui.pushToast('Failed to load list: Invalid JSON', 'error')
    } finally {
      if (fileInput.value) fileInput.value.value = ''
    }
  }
  reader.readAsText(file)
}

const BIB_BATCH_SIZE = 50
const BIB_ENTRY_RE = /@(?=\w+\s*\{)/g

function splitBibEntries(text: string): string[] {
  const positions: number[] = []
  let match: RegExpExecArray | null
  BIB_ENTRY_RE.lastIndex = 0
  while ((match = BIB_ENTRY_RE.exec(text)) !== null) {
    positions.push(match.index)
  }
  if (positions.length === 0) return []
  const entries: string[] = []
  for (let i = 0; i < positions.length; i++) {
    const start = positions[i]!
    const end = i + 1 < positions.length ? positions[i + 1]! : text.length
    entries.push(text.slice(start, end))
  }
  return entries
}

function triggerBibImport(mode: 'append' | 'replace') {
  bibMode.value = mode
  bibShowModePopover.value = false
  bibFileInput.value?.click()
}

async function handleBibFileLoad(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  bibImporting.value = true
  bibProgress.value = 0
  bibStatus.value = 'Reading file...'
  bibUnmatched.value = []
  bibShowUnmatched.value = true

  try {
    const text = await file.text()
    if (!text.trim()) {
      ui.pushToast('BibTeX file is empty', 'error')
      return
    }

    const entries = splitBibEntries(text)
    if (entries.length === 0) {
      ui.pushToast('No BibTeX entries found in file', 'error')
      return
    }

    // Chunk into batches
    const batches: string[] = []
    for (let i = 0; i < entries.length; i += BIB_BATCH_SIZE) {
      batches.push(entries.slice(i, i + BIB_BATCH_SIZE).join('\n'))
    }

    const stagedMatched: BibtexMatchedItem[] = []
    const stagedUnmatched: BibtexUnmatchedItem[] = []
    let failedEntryCount = 0
    let allBatchesOk = true

    // INVARIANT: Replace mode only clears existing selection if ALL batches succeed.
    // If any batch fails (network/server error), we degrade to Append mode to
    // prevent data loss. This is the critical safety contract — see spec section
    // "Frontend Logic" step 7 and Task 7 in the implementation plan.

    for (let i = 0; i < batches.length; i++) {
      bibStatus.value = `Matching ${Math.min((i + 1) * BIB_BATCH_SIZE, entries.length)}/${entries.length}...`
      bibProgress.value = Math.round(((i + 1) / batches.length) * 100)

      try {
        const result = await matchBibtex(batches[i]!)
        stagedMatched.push(...result.matched)
        stagedUnmatched.push(...result.unmatched)
      } catch (err) {
        console.error(`Batch ${i + 1} failed:`, err)
        allBatchesOk = false
        // Count entries in failed batch
        const batchEntryCount = splitBibEntries(batches[i]!).length
        failedEntryCount += batchEntryCount || BIB_BATCH_SIZE
      }
    }

    // Commit staged results — store methods are async (IndexedDB), must await
    if (bibMode.value === 'replace' && allBatchesOk) {
      await selection.clear()
    }

    for (const m of stagedMatched) {
      await selection.add({
        paper_id: m.paper_id,
        title: m.title,
        year: m.year ?? '',
        venue: m.venue ?? '',
        authors: m.authors,
      } as any) // SearchItem optional fields will be undefined
    }

    bibUnmatched.value = stagedUnmatched

    // Build toast message
    const parts: string[] = [`Matched ${stagedMatched.length}`]
    if (stagedUnmatched.length > 0) parts.push(`not found ${stagedUnmatched.length}`)
    if (failedEntryCount > 0) parts.push(`failed ${failedEntryCount}`)
    const toastType = failedEntryCount > 0 ? 'error' as const : 'success' as const
    ui.pushToast(parts.join(', '), toastType)

    if (!allBatchesOk && bibMode.value === 'replace') {
      ui.pushToast('Replace cancelled due to batch failures — items appended instead', 'error')
    }

    bibStatus.value = 'Done'
  } catch (err) {
    console.error(err)
    ui.pushToast('Failed to import BibTeX', 'error')
  } finally {
    bibImporting.value = false
    if (bibFileInput.value) bibFileInput.value.value = ''
  }
}


</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-foreground dark:text-ink-100">{{ t('selectedTitle') }}</h1>
        <p class="text-sm text-muted-foreground dark:text-ink-400">
          {{ t('readingQueue') }} ({{ selection.count }} / {{ MAX_BATCH_SIZE }})
        </p>
      </div>
      <div class="flex flex-wrap gap-2">
        <input
          ref="fileInput"
          type="file"
          accept=".json"
          class="hidden"
          @change="handleFileLoad"
        />
        <input
          ref="bibFileInput"
          type="file"
          accept=".bib"
          class="hidden"
          @change="handleBibFileLoad"
        />
        <Button variant="outline" size="sm" @click="saveList" :disabled="selection.count === 0">
          <Save class="mr-2 h-4 w-4" /> {{ t('saveList') }}
        </Button>
        <Button variant="outline" size="sm" @click="triggerLoadList">
          <Upload class="mr-2 h-4 w-4" /> {{ t('loadList') }}
        </Button>
        <div class="relative">
          <Button variant="outline" size="sm" @click="bibShowModePopover = !bibShowModePopover">
            <FileUp class="mr-2 h-4 w-4" /> {{ t('importBibtex') || 'Import BibTeX' }}
          </Button>
          <div
            v-if="bibShowModePopover"
            class="absolute right-0 top-full z-10 mt-1 w-40 rounded-md border border-border/60 bg-popover p-1 text-popover-foreground shadow-lg dark:border-ink-700 dark:bg-ink-900"
          >
            <button
              class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-muted dark:hover:bg-ink-800"
              @click="triggerBibImport('append')"
            >
              Append
            </button>
            <button
              class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-muted dark:hover:bg-ink-800"
              @click="triggerBibImport('replace')"
            >
              Replace
            </button>
          </div>
        </div>
        <Button variant="outline" size="sm" @click="selection.clear" :disabled="selection.count === 0">
          <Trash2 class="mr-2 h-4 w-4" /> {{ t('clear') }}
        </Button>
        <Button size="sm" :disabled="!canDownload || downloading" @click="downloadAll">
          <Download class="mr-2 h-4 w-4" />
          {{ downloadButtonLabel }}
        </Button>
      </div>
    </div>

    <div
      v-if="selection.count > 0"
      class="rounded-xl border border-border/60 bg-card p-4 text-card-foreground shadow-sm dark:border-ink-700 dark:bg-ink-900/80"
      data-testid="selected-export-options"
    >
      <div class="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <div class="space-y-2">
          <div class="text-sm font-semibold text-foreground dark:text-ink-100">{{ t('selectedExportFormat') }}</div>
          <div class="flex gap-2">
            <button
              type="button"
              class="rounded-md border px-3 py-1.5 text-sm"
              :class="exportMode === 'zip' ? 'border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-500/15 dark:text-blue-200' : 'border-border text-muted-foreground hover:bg-muted dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800'"
              @click="setExportMode('zip')"
            >
              {{ t('selectedExportZip') }}
            </button>
            <button
              type="button"
              class="rounded-md border px-3 py-1.5 text-sm"
              :class="exportMode === 'jsonl' ? 'border-blue-500 bg-blue-50 text-blue-700 dark:border-blue-400 dark:bg-blue-500/15 dark:text-blue-200' : 'border-border text-muted-foreground hover:bg-muted dark:border-ink-700 dark:text-ink-300 dark:hover:bg-ink-800'"
              @click="setExportMode('jsonl')"
            >
              {{ t('selectedExportJsonl') }}
            </button>
          </div>
        </div>

        <div class="space-y-3">
          <div class="text-sm font-semibold text-foreground dark:text-ink-100">{{ t('selectedExportIncludeContent') }}</div>
          <div class="flex flex-wrap gap-x-4 gap-y-2 text-sm text-foreground/80 dark:text-ink-300">
            <label class="inline-flex items-center gap-2">
              <input v-model="includeMetadata" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
              {{ t('selectedExportMetadata') }}
            </label>
            <template v-if="exportMode === 'zip'">
              <label class="inline-flex items-center gap-2">
                <input v-model="includePdf" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
                {{ t('selectedExportPdf') }}
              </label>
              <label class="inline-flex items-center gap-2">
                <input v-model="includeSourceMarkdown" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
                {{ t('selectedExportSourceMarkdown') }}
              </label>
              <label class="inline-flex items-center gap-2">
                <input v-model="includeTranslatedMarkdown" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
                {{ t('selectedExportTranslatedMarkdown') }}
              </label>
              <label class="inline-flex items-center gap-2">
                <input v-model="includeImages" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
                {{ t('selectedExportImages') }}
              </label>
            </template>
          </div>

          <div class="space-y-2">
            <label class="inline-flex items-center gap-2 text-sm font-semibold text-foreground dark:text-ink-100">
              <input v-model="includeSummaries" type="checkbox" class="rounded border-ink-300 accent-blue-500 dark:border-ink-600" />
              <span>{{ t('selectedExportSummaryTemplates') }}</span>
              <span v-if="summaryTemplatesLoading" class="text-xs font-normal text-muted-foreground dark:text-ink-400">{{ t('selectedExportLoadingTemplates') }}</span>
            </label>
            <div v-if="includeSummaries && availableSummaryTemplates.length > 0" class="flex flex-wrap gap-x-4 gap-y-2 text-sm text-foreground/80 dark:text-ink-300">
              <label
                v-for="template in availableSummaryTemplates"
                :key="template"
                class="inline-flex items-center gap-2"
              >
                <input
                  type="checkbox"
                  class="rounded border-ink-300 accent-blue-500 dark:border-ink-600"
                  :checked="selectedSummaryTemplates.includes(template)"
                  @change="toggleSummaryTemplate(template)"
                />
                <span class="font-mono text-xs">{{ template }}</span>
              </label>
            </div>
            <div v-else-if="includeSummaries" class="text-sm text-muted-foreground dark:text-ink-400">
              {{ summaryTemplatesLoading ? t('selectedExportLoadingTemplates') : t('selectedExportNoSummaryTemplates') }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <div v-if="downloading" class="rounded-xl border border-blue-100 bg-blue-50 p-4">
      <div class="space-y-2">
        <div class="flex justify-between text-sm text-blue-700">
          <span>{{ status }}</span>
          <span>{{ progress }}%</span>
        </div>
        <Progress :model-value="progress" class="h-2" />
        <div v-if="sizeBytes" class="text-xs text-blue-600">
          {{ t('downloaded') }}: {{ (sizeBytes / 1024 / 1024).toFixed(2) }} MB
        </div>
      </div>
    </div>

    <div v-if="bibImporting" class="rounded-xl border border-blue-100 bg-blue-50 p-4">
      <div class="space-y-2">
        <div class="flex justify-between text-sm text-blue-700">
          <span>{{ bibStatus }}</span>
          <span>{{ bibProgress }}%</span>
        </div>
        <Progress :model-value="bibProgress" class="h-2" />
      </div>
    </div>

    <div
      v-if="bibUnmatched.length > 0"
      class="rounded-xl border border-amber-200 bg-amber-50 p-4"
    >
      <button
        class="flex w-full items-center justify-between text-sm font-medium text-amber-800"
        @click="bibShowUnmatched = !bibShowUnmatched"
      >
        <span>⚠ {{ bibUnmatched.length }} papers not found in database</span>
        <span class="text-xs">{{ bibShowUnmatched ? '▲' : '▼' }}</span>
      </button>
      <ul v-if="bibShowUnmatched" class="mt-2 space-y-1">
        <li
          v-for="item in bibUnmatched"
          :key="item.bibtex_key"
          class="flex items-center justify-between text-sm text-amber-700"
        >
          <span class="truncate">"{{ item.title || item.bibtex_key }}"</span>
          <a
            :href="`/?q=${encodeURIComponent(item.search_query)}`"
            target="_blank"
            class="ml-2 shrink-0 text-xs text-blue-600 hover:underline"
          >
            Search →
          </a>
        </li>
      </ul>
    </div>

    <div v-if="selection.count === 0" class="flex min-h-[300px] flex-col items-center justify-center rounded-xl border-2 border-dashed border-ink-200 bg-ink-50 p-8 text-center text-ink-500">
      <FileDown class="mb-4 h-12 w-12 text-ink-300" />
      <h3 class="text-lg font-medium text-ink-700">{{ t('noPapers') }}</h3>
      <p class="mt-2 max-w-sm">
        {{ t('noPapersDesc') }}
      </p>
    </div>

    <div v-else class="space-y-3">
      <SearchResultItem
        v-for="(item, index) in selection.items"
        :key="item.paper_id"
        :item="item"
        :display-index="index + 1"
        :is-selected="true"
        :selection-full="selection.isFull"
        :expanded="expanded[item.paper_id]"
        :expanded-markdown="expandedMarkdown[item.paper_id]"
        :expanded-loading="expandedLoading[item.paper_id]"
        :snippet-renderer="snippetRenderer"
        @toggle-select="selection.toggle(item)"
        @toggle-summary="toggleSummary(item)"
      />
    </div>
  </div>
</template>
