<script setup lang="ts">
import { onUnmounted, ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { fetchJson, type PaperDetail } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { useUiStore } from '@/stores/ui'
import { useSelectionStore } from '@/stores/selection'
import { useRuntimeConfigStore } from '@/stores/runtime-config'
import { useElementBounding, useMediaQuery, useWindowSize, refDebounced } from '@vueuse/core'
import SummaryPanel from '@/components/SummaryPanel.vue'
import MarkdownPanel from '@/components/MarkdownPanel.vue'
import PdfViewer from '@/components/PdfViewer.vue'
import { usePaperDetail } from '@/composables/usePaperDetail'
import { useSplitView, type ViewMode } from '@/composables/useSplitView'
import MetadataPanel from '@/components/paper-detail/MetadataPanel.vue'
import { resolveStaticBaseUrl } from '@/lib/static-base'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const { t } = useI18n()
const ui = useUiStore()
const selection = useSelectionStore()
const runtimeConfig = useRuntimeConfigStore()

const paperId = computed(() => String(route.params.paperId || ''))
const { detailQuery } = usePaperDetail(paperId)
const detail = computed<PaperDetail | null>(() => detailQuery.data.value ?? null)
const loading = computed(() => detailQuery.isLoading.value)
const error = computed(() => (detailQuery.error.value ? 'Failed to load paper detail.' : ''))

const prevId = computed(() => selection.getPrevId(paperId.value))
const nextId = computed(() => selection.getNextId(paperId.value))

function navigateTo(id: string | null) {
  if (id) router.push(`/paper/${id}`)
}

const summaryTemplate = ref('')
const summaryMarkdown = ref('')
const summaryUrls = ref<Record<string, string>>({})
const summaryAvailable = ref<string[]>([])
const summaryMeta = ref<Record<string, any> | null>(null)

const {
  viewMode,
  contentTab,
  leftView,
  rightView,
  splitPercent,
  detailWidthPercent,
  swapSplit,
  widenLeft,
  tightenLeft,
} =
  useSplitView()
const showMetadata = ref(true)
const isZenMode = ref(false)
const isLarge = useMediaQuery('(min-width: 1024px)')
const panelRef = ref<HTMLElement | null>(null)
const splitRef = ref<HTMLElement | null>(null)
const { height: windowHeight } = useWindowSize()
const panelBounds = useElementBounding(panelRef)
const splitBounds = useElementBounding(splitRef)

// Debounce layout triggers to prevent performance explosion during resize/scroll
const windowHeightDebounced = refDebounced(windowHeight, 200)
const panelTopDebounced = refDebounced(panelBounds.top, 200)
const splitTopDebounced = refDebounced(splitBounds.top, 200)

const imagesBaseUrl = computed(() => {
  const translatedUrl = detail.value?.translated_md_urls
    ? Object.values(detail.value.translated_md_urls)[0]
    : undefined
  return resolveStaticBaseUrl(
    runtimeConfig.staticBaseUrl,
    detail.value?.images_base_url,
    detail.value?.manifest_url,
    detail.value?.summary_url,
    detail.value?.source_md_url,
    translatedUrl,
    detail.value?.pdf_url
  )
})
const hasPdf = computed(() => Boolean(detail.value?.pdf_url))
const hasSource = computed(() => Boolean(detail.value?.source_md_url))
const translatedLangs = computed(() => Object.keys(detail.value?.translated_md_urls || {}))
const hasTranslated = computed(() => translatedLangs.value.length > 0)
const hasMarkdown = computed(() => hasSource.value || hasTranslated.value)

const viewOptions = computed(() => {
  const options: Array<{ value: string; label: string }> = []
  if (Object.keys(summaryUrls.value).length) {
    options.push({ value: 'summary', label: t('summary') })
  }
  if (hasSource.value) {
    options.push({ value: 'source', label: t('source') })
  }
  for (const lang of translatedLangs.value) {
    options.push({ value: `translated:${lang}`, label: `${t('translated')} (${lang})` })
  }
  if (hasPdf.value) {
    options.push({ value: 'pdf', label: t('pdfViewer') })
  }
  return options
})

const markdownOptions = computed(() => {
  const options: Array<{ value: string; label: string }> = []
  if (hasSource.value) {
    options.push({ value: 'source', label: t('source') })
  }
  for (const lang of translatedLangs.value) {
    options.push({ value: `translated:${lang}`, label: `${t('translated')} (${lang})` })
  }
  return options
})

const splitGridStyle = computed(() => {
  if (!isLarge.value) return {}
  return {
    gridTemplateColumns: `${splitPercent.value}% ${100 - splitPercent.value}%`,
  }
})
const splitAreaStyle = computed(() => {
  if (!isLarge.value) return {}
  if (isZenMode.value) return { height: 'calc(100vh - 60px)' }
  const top = splitTopDebounced.value || 0
  const viewport = windowHeightDebounced.value || 0
  if (!viewport) return {}
  const height = Math.max(320, Math.floor(viewport - top - 24))
  return { height: `${height}px` }
})
const splitLeftIsPdf = computed(() => leftView.value === 'pdf')
const splitRightIsPdf = computed(() => rightView.value === 'pdf')
// In Zen Mode, allow width control (remove isZenMode check). Only Split view forces full width.
const contentWidthClass = computed(() => (viewMode.value === 'split' ? 'w-full' : 'mx-auto w-full'))
const contentWidthStyle = computed(() => {
  if (viewMode.value === 'split') return {}
  return { maxWidth: `${detailWidthPercent.value}vw` }
})
const panelHeightStyle = computed(() => {
  if (isZenMode.value) return { height: '100%' }
  const top = panelTopDebounced.value || 0
  const viewport = windowHeightDebounced.value || 0
  if (!viewport) return { minHeight: '70vh' }
  const height = Math.max(320, Math.floor(viewport - top - 24))
  return { height: `${height}px`, minHeight: '70vh' }
})

async function loadSummary(url: string) {
  try {
    const data = await fetchJson<any>(url)
    summaryMarkdown.value = ''
    summaryMeta.value = null
    summaryMarkdown.value = data.summary || ''
    summaryMeta.value = {
      title: data.paper_title || data.title || '',
      authors: Array.isArray(data.paper_authors) ? data.paper_authors : data.authors || [],
      institutions: Array.isArray(data.paper_institutions) ? data.paper_institutions : data.institutions || [],
      publicationDate: data.publication_date || '',
      venue: data.publication_venue || data.venue || '',
      outputLanguage: data.output_language || '',
      keywords: Array.isArray(data.keywords) ? data.keywords : [],
      abstract: data.abstract || '',
    }
    if (Array.isArray(data.available_templates) && data.available_templates.length) {
      summaryAvailable.value = data.available_templates.map((item: string) => String(item))
    }
  } catch (err) {
    ui.pushToast('Failed to load summary', 'error')
  }
}

function resolveMarkdownUrl(view: string) {
  if (!detail.value) return ''
  if (view === 'source') return detail.value.source_md_url || ''
  if (view.startsWith('translated:')) {
    const lang = view.replace('translated:', '')
    return detail.value.translated_md_urls?.[lang] || ''
  }
  return ''
}

function goFacet(facet: string, value: string) {
  router.push(`/facet/${facet}/${encodeURIComponent(value)}`)
}

function toggleZenMode() {
  isZenMode.value = !isZenMode.value
}

watch(summaryTemplate, async (value) => {
  if (value && summaryUrls.value?.[value]) {
    await loadSummary(summaryUrls.value[value])
  }
})

watch(isLarge, (value) => {
  if (!value && viewMode.value === 'split') {
    viewMode.value = 'summary'
  }
})

onUnmounted(() => {
  ui.setDetailHeader('', '')
})

watch(
  () => detail.value,
  async (data) => {
    if (!data) return
    ui.setDetailHeader(
      String(data.title || ''),
      `${data.venue || ''}${data.year ? ` · ${data.year}` : ''}`.trim()
    )
    const urls = { ...(data.summary_urls || {}) }
    if (data.summary_url && !urls.default) {
      urls.default = data.summary_url
    }
    summaryUrls.value = urls
    summaryAvailable.value = Object.keys(summaryUrls.value)
    const templates = Object.keys(summaryUrls.value)
    const targetTemplate = (summaryUrls.value.simple ? 'simple' : '') || data.preferred_summary_template || templates[0] || ''
    
    // Reset content for new paper
    summaryMarkdown.value = ''
    summaryMeta.value = null
    
    // 1. Determine effective template
    let nextTemplate = targetTemplate
    if (route.query.template && summaryUrls.value[String(route.query.template)]) {
      nextTemplate = String(route.query.template)
    }

    if (summaryTemplate.value === nextTemplate && nextTemplate) {
      // If template name is same, manual trigger needed (watcher won't fire)
      const url = summaryUrls.value[nextTemplate]
      if (url) {
        await loadSummary(url)
      }
    }
    summaryTemplate.value = nextTemplate

    // 2. Determine views from URL or defaults
    if (route.query.view) {
      const view = String(route.query.view)
      if (['summary', 'markdown', 'pdf', 'split'].includes(view)) {
        viewMode.value = view as ViewMode
      }
    } else {
      viewMode.value = 'summary' // Default
    }

    if (route.query.left) {
      leftView.value = String(route.query.left)
    } else {
      // Default left logic
      if (hasTranslated.value) {
        const [firstLang] = translatedLangs.value
        leftView.value = `translated:${firstLang}`
      } else if (hasSource.value) {
        leftView.value = 'source'
      } else {
        leftView.value = 'summary'
      }
    }

    if (route.query.right) {
      rightView.value = String(route.query.right)
    } else {
      rightView.value = 'summary' // Default right
    }

    // Set tab content if applicable
    if (viewMode.value === 'markdown' || viewMode.value === 'split') {
       // logic to sync contentTab with leftView if logical? 
       // Just keep contentTab independent or sync with view.
       if (leftView.value.startsWith('translated:') || leftView.value === 'source') {
         contentTab.value = leftView.value
       }
    }
  },
  { immediate: true }
)

// Sync state to URL
watch([viewMode, leftView, rightView, summaryTemplate], () => {
  router.replace({
    query: {
      ...route.query,
      view: viewMode.value,
      left: viewMode.value === 'split' ? leftView.value : undefined,
      right: viewMode.value === 'split' ? rightView.value : undefined,
      template: summaryTemplate.value || undefined
    }
  })
})
</script>

<template>
  <div v-if="loading" class="rounded-xl border border-ink-100 bg-white p-6 text-sm text-ink-500">
    {{ t('loadingPaperDetail') }}
  </div>
  <div v-else-if="error" class="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-600">
    {{ error }}
  </div>
  <div v-else-if="detail" class="space-y-4">

    <div
      :class="[
        'grid items-start gap-6',
        showMetadata ? 'grid-cols-[320px_minmax(0,_1fr)] max-sm:grid-cols-1' : 'grid-cols-1',
      ]"
    >
      <aside v-if="showMetadata" class="space-y-4">
        <MetadataPanel
          :detail="detail"
          :translated-langs="translatedLangs"
          :on-facet-click="goFacet"
        />
      </aside>

      <section
        :class="[
          'flex min-h-0 min-w-0 flex-1 flex-col gap-4',
          isZenMode ? 'fixed inset-0 z-[100] bg-background p-4' : ''
        ]"
      >
        <div class="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-500">
          <div class="flex flex-wrap items-center gap-2 lg:flex-nowrap">
            <template v-if="selection.selectedIds.has(paperId)">
              <div class="flex items-center gap-1 border-r border-ink-200 pr-2 mr-1">
                <Button size="icon-sm" variant="ghost" :disabled="!prevId" @click="navigateTo(prevId)" title="Previous in selection">
                  <ChevronLeft class="h-4 w-4" />
                </Button>
                <span class="text-[10px] font-medium text-ink-400">Queue</span>
                <Button size="icon-sm" variant="ghost" :disabled="!nextId" @click="navigateTo(nextId)" title="Next in selection">
                  <ChevronRight class="h-4 w-4" />
                </Button>
              </div>
            </template>
            <span class="font-semibold text-ink-700">{{ t('view') }}</span>
            <Button
              size="sm"
              :variant="viewMode === 'summary' ? 'default' : 'outline'"
              :aria-pressed="viewMode === 'summary'"
              @click="viewMode = 'summary'"
            >
              {{ t('summary') }}
            </Button>
            <Button
              size="sm"
              :variant="viewMode === 'markdown' ? 'default' : 'outline'"
              :aria-pressed="viewMode === 'markdown'"
              :disabled="!hasMarkdown"
              @click="viewMode = 'markdown'"
            >
              {{ t('markdown') }}
            </Button>
            <Button
              size="sm"
              :variant="viewMode === 'pdf' ? 'default' : 'outline'"
              :aria-pressed="viewMode === 'pdf'"
              :disabled="!hasPdf"
              @click="viewMode = 'pdf'"
            >
              {{ t('pdfViewer') }}
            </Button>
            <Button
              v-if="isLarge"
              size="sm"
              :variant="viewMode === 'split' ? 'default' : 'outline'"
              :aria-pressed="viewMode === 'split'"
              :disabled="!hasMarkdown"
              @click="viewMode = 'split'"
            >
              {{ t('split') }}
            </Button>
            <template v-if="viewMode === 'split' && isLarge">
              <span class="mx-2 hidden h-4 w-px bg-ink-200 lg:inline-block"></span>
              <span class="font-semibold text-ink-700">{{ t('split') }}</span>
              <Button size="sm" variant="outline" @click="tightenLeft">Left -</Button>
              <Button size="sm" variant="outline" @click="swapSplit">Swap</Button>
              <Button size="sm" variant="outline" @click="widenLeft">Left +</Button>
              <div class="flex items-center gap-2">
                <input
                  v-model.lazy.number="splitPercent"
                  type="range"
                  min="30"
                  max="70"
                  step="5"
                  class="w-28"
                />
                <span class="text-[11px] text-ink-500">{{ splitPercent }} / {{ 100 - splitPercent }}</span>
              </div>
            </template>
            <template v-else>
              <span class="mx-2 hidden h-4 w-px bg-ink-200 lg:inline-block"></span>
              <span class="font-semibold text-ink-700">Width</span>
              <div class="flex items-center gap-2">
                <input
                  v-model.lazy.number="detailWidthPercent"
                  type="range"
                  min="40"
                  max="100"
                  step="5"
                  class="w-28"
                />
                <span class="text-[11px] text-ink-500">{{ detailWidthPercent }}%</span>
              </div>
            </template>
            <Button size="sm" :variant="isZenMode ? 'default' : 'outline'" @click="toggleZenMode">
              {{ isZenMode ? t('exitZenMode') : t('zenMode') }}
            </Button>
          </div>
          <Button v-if="!isZenMode" size="sm" variant="outline" @click="showMetadata = !showMetadata">
            {{ showMetadata ? t('hideMetadata') : t('showMetadata') }}
          </Button>
        </div>

        <div :class="{'flex-1 min-h-0 overflow-y-auto w-full': isZenMode}">
          <div v-if="viewMode === 'summary'" :class="['space-y-4', contentWidthClass]" :style="contentWidthStyle">
            <Card>
              <CardHeader>
                <CardTitle class="text-sm">{{ t('summaryTemplate') }}</CardTitle>
              </CardHeader>
              <CardContent>
                <SummaryPanel
                  v-model="summaryTemplate"
                  :summary-urls="summaryUrls"
                  :summary-available="summaryAvailable"
                  :summary-markdown="summaryMarkdown"
                  :summary-meta="summaryMeta"
                  :images-base-url="imagesBaseUrl"
                />
                <div class="mt-3 flex flex-wrap gap-2 text-xs">
                  <Button
                    v-if="summaryTemplate"
                    size="sm"
                    variant="outline"
                    @click="goFacet('summary_templates', summaryTemplate)"
                  >
                    {{ t('viewTemplateStats') }}
                  </Button>
                </div>
              </CardContent>
            </Card>
          </div>

          <div v-else-if="viewMode === 'markdown'" :class="['space-y-4', contentWidthClass]" :style="contentWidthStyle">
            <Card>
              <CardHeader>
                <CardTitle class="text-sm">{{ t('markdown') }}</CardTitle>
              </CardHeader>
              <CardContent>
                <Tabs v-model="contentTab" class="w-full">
                  <TabsList class="flex flex-wrap gap-2">
                    <TabsTrigger v-for="option in markdownOptions" :key="option.value" :value="option.value">
                      {{ option.label }}
                    </TabsTrigger>
                  </TabsList>
                  <TabsContent :value="contentTab">
                    <MarkdownPanel
                      :url="resolveMarkdownUrl(contentTab)"
                      :images-base-url="imagesBaseUrl"
                      placeholder="No markdown available."
                    />
                  </TabsContent>
                </Tabs>
              </CardContent>
            </Card>
          </div>

          <div v-else-if="viewMode === 'pdf'" :class="['space-y-4', contentWidthClass]" :style="contentWidthStyle">
            <Card class="flex min-h-0 flex-col" :class="{'h-full': isZenMode}">
              <CardHeader>
                <CardTitle class="text-sm">{{ t('pdfViewer') }}</CardTitle>
              </CardHeader>
              <CardContent ref="panelRef" class="flex-1 min-h-0 overflow-hidden" :style="panelHeightStyle">
                <PdfViewer :url="detail.pdf_url" full-height />
              </CardContent>
            </Card>
          </div>

          <div v-else ref="splitRef" class="flex min-h-0 flex-col gap-3 overflow-hidden" :style="splitAreaStyle">
            <div class="grid flex-1 min-h-0 gap-4" :style="splitGridStyle">
              <Card class="flex h-full min-h-0 flex-col">
                <CardHeader class="flex-row items-center justify-between">
                  <CardTitle class="text-sm">Left</CardTitle>
                  <Select v-model="leftView">
                    <SelectTrigger class="w-[200px]">
                      <SelectValue placeholder="Select view" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="option in viewOptions" :key="option.value" :value="option.value">
                        {{ option.label }}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </CardHeader>
                <CardContent
                  :class="[
                    'flex-1 min-h-0',
                    splitLeftIsPdf ? 'overflow-hidden' : 'overflow-auto',
                  ]"
                >
                  <template v-if="leftView === 'summary'">
                    <SummaryPanel
                      v-model="summaryTemplate"
                      :summary-urls="summaryUrls"
                      :summary-available="summaryAvailable"
                      :summary-markdown="summaryMarkdown"
                      :summary-meta="summaryMeta"
                      :images-base-url="imagesBaseUrl"
                    />
                    <div v-if="summaryTemplate" class="mt-2">
                      <Button size="sm" variant="outline" @click="goFacet('summary_templates', summaryTemplate)" >
                        {{ t('viewTemplateStats') }}
                      </Button>
                    </div>
                  </template>
                  <PdfViewer v-else-if="leftView === 'pdf'" :url="detail.pdf_url" fit />
                  <MarkdownPanel
                    v-else
                    :url="resolveMarkdownUrl(leftView)"
                    :images-base-url="imagesBaseUrl"
                    placeholder="No markdown available."
                  />
                </CardContent>
              </Card>

              <Card class="flex h-full min-h-0 flex-col">
                <CardHeader class="flex-row items-center justify-between">
                  <CardTitle class="text-sm">Right</CardTitle>
                  <Select v-model="rightView">
                    <SelectTrigger class="w-[200px]">
                      <SelectValue placeholder="Select view" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem v-for="option in viewOptions" :key="option.value" :value="option.value">
                        {{ option.label }}
                      </SelectItem>
                    </SelectContent>
                  </Select>
                </CardHeader>
                <CardContent
                  :class="[
                    'flex-1 min-h-0',
                    splitRightIsPdf ? 'overflow-hidden' : 'overflow-auto',
                  ]"
                >
                  <template v-if="rightView === 'summary'">
                    <SummaryPanel
                      v-model="summaryTemplate"
                      :summary-urls="summaryUrls"
                      :summary-available="summaryAvailable"
                      :summary-markdown="summaryMarkdown"
                      :summary-meta="summaryMeta"
                      :images-base-url="imagesBaseUrl"
                    />
                    <div v-if="summaryTemplate" class="mt-2">
                      <Button size="sm" variant="outline" @click="goFacet('summary_templates', summaryTemplate)">
                        {{ t('viewTemplateStats') }}
                      </Button>
                    </div>
                  </template>
                  <PdfViewer v-else-if="rightView === 'pdf'" :url="detail.pdf_url" fit />
                  <MarkdownPanel
                    v-else
                    :url="resolveMarkdownUrl(rightView)"
                    :images-base-url="imagesBaseUrl"
                    placeholder="No markdown available."
                  />
                </CardContent>
              </Card>
            </div>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
