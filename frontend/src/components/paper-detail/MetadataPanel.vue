<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { PaperDetail } from '@/lib/api'
import { getPaperBibtex } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Copy, Check, ExternalLink } from 'lucide-vue-next'

const props = defineProps<{
  detail: PaperDetail
  translatedLangs: string[]
  onFacetClick: (facet: string, value: string) => void
}>()

const { t } = useI18n()

const bibtexRaw = ref('')
const bibtexLoading = ref(false)
const bibtexError = ref(false)
const bibtexLoaded = ref(false)
const bibtexCopied = ref(false)
const bibtexExpanded = ref(false)

async function loadBibtex() {
  if (bibtexLoaded.value || bibtexLoading.value) return
  bibtexLoading.value = true
  bibtexError.value = false
  try {
    const data = await getPaperBibtex(props.detail.paper_id)
    bibtexRaw.value = data.bibtex_raw
    bibtexLoaded.value = true
  } catch {
    bibtexError.value = true
    bibtexLoaded.value = true
  } finally {
    bibtexLoading.value = false
  }
}

async function copyBibtex() {
  if (!bibtexRaw.value) return
  try {
    await navigator.clipboard.writeText(bibtexRaw.value)
    bibtexCopied.value = true
    setTimeout(() => { bibtexCopied.value = false }, 2000)
  } catch {
    const textarea = document.createElement('textarea')
    textarea.value = bibtexRaw.value
    textarea.style.position = 'fixed'
    textarea.style.opacity = '0'
    document.body.appendChild(textarea)
    textarea.select()
    document.execCommand('copy')
    document.body.removeChild(textarea)
    bibtexCopied.value = true
    setTimeout(() => { bibtexCopied.value = false }, 2000)
  }
}

watch(() => props.detail.paper_id, () => {
  bibtexRaw.value = ''
  bibtexLoading.value = false
  bibtexError.value = false
  bibtexLoaded.value = false
  bibtexCopied.value = false
  bibtexExpanded.value = false
})
</script>

<template>
  <Card class="shadow-card border-ink-200 dark:border-ink-700">
    <CardHeader>
      <CardTitle class="text-sm font-semibold text-ink-800 dark:text-ink-200">{{ t('metadata') }}</CardTitle>
    </CardHeader>
    <CardContent class="space-y-5 text-sm text-ink-600 dark:text-ink-400 lg:max-h-[calc(100vh-240px)] lg:overflow-auto">
      <!-- Authors — primary navy blue -->
      <div class="flex flex-wrap gap-1.5">
        <TooltipProvider>
          <Tooltip v-for="author in detail.authors" :key="author">
            <TooltipTrigger as-child>
              <Badge
                role="button"
                tabindex="0"
                class="cursor-pointer bg-accent-50 text-accent-800 border-accent-200 hover:bg-accent-100 dark:bg-accent-900/30 dark:text-accent-200 dark:border-accent-800 dark:hover:bg-accent-900/50 transition-colors"
                variant="outline"
                @click="onFacetClick('authors', author)"
                @keydown.enter.prevent="onFacetClick('authors', author)"
                @keydown.space.prevent="onFacetClick('authors', author)"
              >
                {{ author }}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>{{ t('authors') }}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>

      <!-- DOI -->
      <div v-if="detail.doi" class="space-y-1.5">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-400 dark:text-ink-500">{{ t('doi') }}</span>
        <div>
          <a
            :href="`https://doi.org/${detail.doi}`"
            target="_blank"
            rel="noopener noreferrer"
            class="inline-flex items-center gap-1.5 rounded-md bg-ink-100 px-2.5 py-1 text-xs font-mono text-accent-600 hover:bg-accent-50 hover:text-accent-700 dark:bg-ink-800 dark:text-accent-400 dark:hover:bg-ink-700 dark:hover:text-accent-300 break-all transition-colors"
          >
            {{ detail.doi }}
            <ExternalLink class="h-3 w-3 shrink-0" />
          </a>
        </div>
      </div>

      <!-- Keywords — teal (scientific) -->
      <div v-if="detail.keywords?.length">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-400 dark:text-ink-500">{{ t('keywords') }}</span>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <Badge
            v-for="kw in detail.keywords"
            :key="kw"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-sci-50 text-sci-800 border-sci-200 hover:bg-sci-100 dark:bg-sci-900/30 dark:text-sci-200 dark:border-sci-800 dark:hover:bg-sci-900/50 transition-colors"
            @click="onFacetClick('keywords', kw)"
          >
            {{ kw }}
          </Badge>
        </div>
      </div>

      <!-- Institutions — subtle slate -->
      <div v-if="detail.institutions?.length">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-400 dark:text-ink-500">{{ t('institutions') }}</span>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <Badge
            v-for="inst in detail.institutions"
            :key="inst"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-ink-100 text-ink-700 border-ink-200 hover:bg-ink-200 dark:bg-ink-800 dark:text-ink-300 dark:border-ink-700 dark:hover:bg-ink-700 transition-colors"
            @click="onFacetClick('institutions', inst)"
          >
            {{ inst }}
          </Badge>
        </div>
      </div>

      <!-- Tags — subtle navy -->
      <div v-if="detail.tags?.length">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-400 dark:text-ink-500">{{ t('tags') }}</span>
        <div class="mt-2 flex flex-wrap gap-1.5">
          <Badge
            v-for="tag in detail.tags"
            :key="tag"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-accent-50 text-accent-700 border-accent-200 hover:bg-accent-100 dark:bg-accent-900/30 dark:text-accent-200 dark:border-accent-800 dark:hover:bg-accent-900/50 transition-colors"
            @click="onFacetClick('tags', tag)"
          >
            {{ tag }}
          </Badge>
        </div>
      </div>

      <!-- Meta badges: language, provider, model — neutral -->
      <div class="flex flex-wrap gap-1.5" v-if="detail.output_language || detail.provider || detail.model">
        <Badge
          v-if="detail.output_language"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-ink-50 text-ink-600 border-ink-200 hover:bg-ink-100 dark:bg-ink-800 dark:text-ink-300 dark:border-ink-700 dark:hover:bg-ink-700 transition-colors"
          @click="onFacetClick('output_languages', detail.output_language)"
        >
          {{ t('outputLang') }}: {{ detail.output_language }}
        </Badge>
        <Badge
          v-if="detail.provider"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-ink-50 text-ink-600 border-ink-200 hover:bg-ink-100 dark:bg-ink-800 dark:text-ink-300 dark:border-ink-700 dark:hover:bg-ink-700 transition-colors"
          @click="onFacetClick('providers', detail.provider)"
        >
          {{ t('providers') }}: {{ detail.provider }}
        </Badge>
        <Badge
          v-if="detail.model"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-ink-50 text-ink-600 border-ink-200 hover:bg-ink-100 dark:bg-ink-800 dark:text-ink-300 dark:border-ink-700 dark:hover:bg-ink-700 transition-colors"
          @click="onFacetClick('models', detail.model)"
        >
          {{ t('models') }}: {{ detail.model }}
        </Badge>
      </div>

      <!-- Prompt template — teal -->
      <div class="flex flex-wrap gap-1.5" v-if="detail.prompt_template">
        <Badge
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-sci-50 text-sci-700 border-sci-200 hover:bg-sci-100 dark:bg-sci-900/30 dark:text-sci-200 dark:border-sci-800 dark:hover:bg-sci-900/50 transition-colors"
          @click="onFacetClick('prompt_templates', detail.prompt_template)"
        >
          {{ t('promptTemplate') }}: {{ detail.prompt_template }}
        </Badge>
      </div>

      <!-- Translation languages — teal -->
      <div class="flex flex-wrap gap-1.5" v-if="translatedLangs.length">
        <Badge
          v-for="lang in translatedLangs"
          :key="lang"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-sci-50 text-sci-700 border-sci-200 hover:bg-sci-100 dark:bg-sci-900/30 dark:text-sci-200 dark:border-sci-800 dark:hover:bg-sci-900/50 transition-colors"
          @click="onFacetClick('translation_langs', lang)"
        >
          {{ t('translationLang') }}: {{ lang }}
        </Badge>
      </div>

      <!-- BibTeX -->
      <div class="space-y-2 border-t border-ink-200 pt-4 dark:border-ink-700">
        <span class="text-[11px] font-semibold uppercase tracking-wider text-ink-400 dark:text-ink-500">{{ t('bibtex') }}</span>
        <div v-if="!bibtexLoaded && !bibtexLoading">
          <Button size="sm" variant="outline" class="text-xs" @click="loadBibtex">
            {{ t('bibtex') }}
          </Button>
        </div>
        <div v-else-if="bibtexLoading" class="flex items-center gap-2 text-xs text-ink-500">
          <svg class="h-3.5 w-3.5 animate-spin" viewBox="0 0 24 24" fill="none">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4" />
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          {{ t('loadingBibtex') }}
        </div>
        <div v-else-if="bibtexError || !bibtexRaw" class="text-xs text-ink-400">
          {{ t('noBibtex') }}
        </div>
        <div v-else class="space-y-2">
          <div class="flex items-center gap-2">
            <Button size="sm" variant="outline" class="gap-1.5 text-xs" @click="copyBibtex">
              <Check v-if="bibtexCopied" class="h-3.5 w-3.5 text-sci-600" />
              <Copy v-else class="h-3.5 w-3.5" />
              {{ bibtexCopied ? t('copiedBibtex') : t('copyBibtex') }}
            </Button>
            <Button size="sm" variant="ghost" class="text-xs" @click="bibtexExpanded = !bibtexExpanded">
              {{ bibtexExpanded ? t('collapse') : t('showMore') }}
            </Button>
          </div>
          <pre
            v-if="bibtexExpanded"
            class="max-h-64 overflow-auto rounded-lg border border-ink-700 bg-ink-900 p-3 text-[11px] leading-relaxed text-ink-200 font-mono"
          >{{ bibtexRaw }}</pre>
        </div>
      </div>

      <!-- PDF Link -->
      <div class="flex flex-wrap gap-2 text-xs" v-if="detail.pdf_url">
        <a
          :href="detail.pdf_url"
          class="inline-flex items-center gap-1 rounded-md bg-ink-100 px-2 py-1 text-accent-600 hover:bg-accent-50 hover:text-accent-700 dark:bg-ink-800 dark:text-accent-400 dark:hover:bg-ink-700 dark:hover:text-accent-300 transition-colors"
          target="_blank"
          rel="noreferrer"
        >
          {{ t('openPdf') }}
          <ExternalLink class="h-3 w-3" />
        </a>
      </div>
    </CardContent>
  </Card>
</template>
