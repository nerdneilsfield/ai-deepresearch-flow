<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Search, ListChecks, Wind, BookOpen, Filter, SplitSquare, Download, FileText, Eye, Lightbulb } from 'lucide-vue-next'

const { t } = useI18n()
</script>

<template>
  <div class="max-w-5xl mx-auto space-y-8 pb-12">
    <div class="space-y-3">
      <h1 class="text-4xl font-bold text-ink-900">{{ t('helpTitle') }}</h1>
      <p class="text-lg text-ink-600">{{ t('helpIntro') }}</p>
    </div>

    <!-- Core Features -->
    <section class="space-y-4">
      <h2 class="text-2xl font-semibold text-ink-800">{{ t('helpCoreFeatures') }}</h2>
      <div class="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-blue-100 p-2 rounded-lg text-blue-600">
              <Search class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpSearchTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpSearchDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpSearchFeature1') }}</li>
              <li>{{ t('helpSearchFeature2') }}</li>
              <li>{{ t('helpSearchFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-purple-100 p-2 rounded-lg text-purple-600">
              <Filter class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpFacetsTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpFacetsDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpFacetsFeature1') }}</li>
              <li>{{ t('helpFacetsFeature2') }}</li>
              <li>{{ t('helpFacetsFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-emerald-100 p-2 rounded-lg text-emerald-600">
              <ListChecks class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpSelectedTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpSelectedDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpSelectedFeature1') }}</li>
              <li>{{ t('helpSelectedFeature2') }}</li>
              <li>{{ t('helpSelectedFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-amber-100 p-2 rounded-lg text-amber-600">
              <Download class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpDownloadTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpDownloadDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpDownloadFeature1') }}</li>
              <li>{{ t('helpDownloadFeature2') }}</li>
            </ul>
          </CardContent>
        </Card>
      </div>
    </section>

    <!-- Reading Experience -->
    <section class="space-y-4">
      <h2 class="text-2xl font-semibold text-ink-800">{{ t('helpReadingExperience') }}</h2>
      <div class="grid gap-6 md:grid-cols-2">
        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-indigo-100 p-2 rounded-lg text-indigo-600">
              <BookOpen class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpSummaryTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpSummaryDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li><strong>KaTeX</strong> - {{ t('helpSummaryKatex') }}</li>
              <li><strong>Mermaid</strong> - {{ t('helpSummaryMermaid') }}</li>
              <li><strong>Markmap</strong> - {{ t('helpSummaryMarkmap') }}</li>
              <li>{{ t('helpSummaryFootnotes') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-teal-100 p-2 rounded-lg text-teal-600">
              <FileText class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpMarkdownTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpMarkdownDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpMarkdownFeature1') }}</li>
              <li>{{ t('helpMarkdownFeature2') }}</li>
              <li>{{ t('helpMarkdownFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-cyan-100 p-2 rounded-lg text-cyan-600">
              <SplitSquare class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpSplitTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpSplitDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpSplitFeature1') }}</li>
              <li>{{ t('helpSplitFeature2') }}</li>
              <li>{{ t('helpSplitFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>

        <Card>
          <CardHeader class="flex flex-row items-center gap-4">
            <div class="bg-rose-100 p-2 rounded-lg text-rose-600">
              <Wind class="h-6 w-6" />
            </div>
            <CardTitle class="text-lg">{{ t('helpZenTitle') }}</CardTitle>
          </CardHeader>
          <CardContent class="space-y-2">
            <p class="text-sm text-ink-600 leading-relaxed">
              {{ t('helpZenDesc') }}
            </p>
            <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
              <li>{{ t('helpZenFeature1') }}</li>
              <li>{{ t('helpZenFeature2') }}</li>
              <li>{{ t('helpZenFeature3') }}</li>
            </ul>
          </CardContent>
        </Card>
      </div>
    </section>

    <!-- Tips & Tricks -->
    <section class="space-y-4">
      <h2 class="text-2xl font-semibold text-ink-800">{{ t('helpTips') }}</h2>
      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-yellow-100 p-2 rounded-lg text-yellow-600">
            <Lightbulb class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('helpTipsTitle') }}</CardTitle>
        </CardHeader>
        <CardContent>
          <ul class="text-sm text-ink-600 space-y-3">
            <li class="flex gap-2">
              <span class="text-ink-400">•</span>
              <span><strong>{{ t('helpTip1Title') }}:</strong> {{ t('helpTip1Desc') }}</span>
            </li>
            <li class="flex gap-2">
              <span class="text-ink-400">•</span>
              <span><strong>{{ t('helpTip2Title') }}:</strong> {{ t('helpTip2Desc') }}</span>
            </li>
            <li class="flex gap-2">
              <span class="text-ink-400">•</span>
              <span><strong>{{ t('helpTip3Title') }}:</strong> {{ t('helpTip3Desc') }}</span>
            </li>
            <li class="flex gap-2">
              <span class="text-ink-400">•</span>
              <span><strong>{{ t('helpTip4Title') }}:</strong> {{ t('helpTip4Desc') }}</span>
            </li>
            <li class="flex gap-2">
              <span class="text-ink-400">•</span>
              <span><strong>{{ t('helpTip5Title') }}:</strong> {{ t('helpTip5Desc') }}</span>
            </li>
          </ul>
        </CardContent>
      </Card>
    </section>

    <!-- Browser Console -->
    <section class="space-y-4">
      <h2 class="text-2xl font-semibold text-ink-800">{{ t('helpDebugTitle') }}</h2>
      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-slate-100 p-2 rounded-lg text-slate-600">
            <Eye class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('helpConsoleTitle') }}</CardTitle>
        </CardHeader>
        <CardContent class="space-y-2">
          <p class="text-sm text-ink-600 leading-relaxed">
            {{ t('helpConsoleDesc') }}
          </p>
          <ul class="text-sm text-ink-600 list-disc list-inside space-y-1 mt-2">
            <li>{{ t('helpConsoleFeature1') }}</li>
            <li>{{ t('helpConsoleFeature2') }}</li>
            <li>{{ t('helpConsoleFeature3') }}</li>
          </ul>
        </CardContent>
      </Card>
    </section>
  </div>
</template>
