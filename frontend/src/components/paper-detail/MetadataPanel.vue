<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { PaperDetail } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'

const props = defineProps<{
  detail: PaperDetail
  translatedLangs: string[]
  onFacetClick: (facet: string, value: string) => void
}>()

const { t } = useI18n()
</script>

<template>
  <Card>
    <CardHeader>
      <CardTitle class="text-sm">{{ t('metadata') }}</CardTitle>
    </CardHeader>
    <CardContent class="space-y-4 text-sm text-ink-600 lg:max-h-[calc(100vh-240px)] lg:overflow-auto">
      <div class="flex flex-wrap gap-2">
        <TooltipProvider>
          <Tooltip v-for="author in detail.authors" :key="author">
            <TooltipTrigger as-child>
              <Badge
                role="button"
                tabindex="0"
                class="cursor-pointer bg-blue-50 text-blue-700 border-blue-200 hover:bg-blue-100 hover:text-blue-800"
                variant="outline"
                @click="onFacetClick('authors', author)"
                @keydown.enter.prevent="onFacetClick('authors', author)"
                @keydown.space.prevent="onFacetClick('authors', author)"
              >
                {{ author }}
              </Badge>
            </TooltipTrigger>
            <TooltipContent>Author</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
      <div v-if="detail.keywords?.length">
        <span class="text-xs uppercase text-ink-400">Keywords</span>
        <div class="mt-2 flex flex-wrap gap-2">
          <Badge
            v-for="kw in detail.keywords"
            :key="kw"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-emerald-50 text-emerald-700 border-emerald-200 hover:bg-emerald-100 hover:text-emerald-800"
            @click="onFacetClick('keywords', kw)"
          >
            {{ kw }}
          </Badge>
        </div>
      </div>
      <div v-if="detail.institutions?.length">
        <span class="text-xs uppercase text-ink-400">Institutions</span>
        <div class="mt-2 flex flex-wrap gap-2">
          <Badge
            v-for="inst in detail.institutions"
            :key="inst"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-indigo-50 text-indigo-700 border-indigo-200 hover:bg-indigo-100 hover:text-indigo-800"
            @click="onFacetClick('institutions', inst)"
          >
            {{ inst }}
          </Badge>
        </div>
      </div>
      <div v-if="detail.tags?.length">
        <span class="text-xs uppercase text-ink-400">{{ t('tags') }}</span>
        <div class="mt-2 flex flex-wrap gap-2">
          <Badge
            v-for="tag in detail.tags"
            :key="tag"
            role="button"
            tabindex="0"
            variant="outline"
            class="cursor-pointer bg-amber-50 text-amber-700 border-amber-200 hover:bg-amber-100 hover:text-amber-800"
            @click="onFacetClick('tags', tag)"
          >
            {{ tag }}
          </Badge>
        </div>
      </div>
      <div class="flex flex-wrap gap-2" v-if="detail.output_language || detail.provider || detail.model">
        <Badge
          v-if="detail.output_language"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-slate-100 text-slate-700 border-slate-200 hover:bg-slate-200"
          @click="onFacetClick('output_languages', detail.output_language)"
        >
          {{ t('outputLang') }}: {{ detail.output_language }}
        </Badge>
        <Badge
          v-if="detail.provider"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-slate-100 text-slate-700 border-slate-200 hover:bg-slate-200"
          @click="onFacetClick('providers', detail.provider)"
        >
          {{ t('providers') }}: {{ detail.provider }}
        </Badge>
        <Badge
          v-if="detail.model"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-slate-100 text-slate-700 border-slate-200 hover:bg-slate-200"
          @click="onFacetClick('models', detail.model)"
        >
          {{ t('models') }}: {{ detail.model }}
        </Badge>
      </div>
      <div class="flex flex-wrap gap-2" v-if="detail.prompt_template">
        <Badge
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-rose-50 text-rose-700 border-rose-200 hover:bg-rose-100 hover:text-rose-800"
          @click="onFacetClick('prompt_templates', detail.prompt_template)"
        >
          {{ t('promptTemplate') }}: {{ detail.prompt_template }}
        </Badge>
      </div>
      <div class="flex flex-wrap gap-2" v-if="translatedLangs.length">
        <Badge
          v-for="lang in translatedLangs"
          :key="lang"
          role="button"
          tabindex="0"
          variant="outline"
          class="cursor-pointer bg-violet-50 text-violet-700 border-violet-200 hover:bg-violet-100 hover:text-violet-800"
          @click="onFacetClick('translation_langs', lang)"
        >
          {{ t('translationLang') }}: {{ lang }}
        </Badge>
      </div>
      <div class="flex flex-wrap gap-2 text-xs">
        <a
          v-if="detail.pdf_url"
          :href="detail.pdf_url"
          class="text-accent-600"
          target="_blank"
          rel="noreferrer"
        >
          {{ t('openPdf') }}
        </a>
      </div>
    </CardContent>
  </Card>
</template>
