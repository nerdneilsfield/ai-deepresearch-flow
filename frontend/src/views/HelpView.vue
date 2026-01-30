<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Search, ListChecks, Wind, BookOpen } from 'lucide-vue-next'

const { t } = useI18n()
</script>

<template>
  <div class="max-w-4xl mx-auto space-y-8">
    <div class="space-y-2">
      <h1 class="text-3xl font-bold text-ink-900">{{ t('helpTitle') }}</h1>
      <p class="text-ink-500">{{ t('helpIntro') }}</p>
    </div>

    <div class="grid gap-6 md:grid-cols-2">
      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-blue-100 p-2 rounded-lg text-blue-600">
            <Search class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('helpSearchTitle') }}</CardTitle>
        </CardHeader>
        <CardContent>
          <p class="text-sm text-ink-600 leading-relaxed">
            {{ t('helpSearchDesc') }}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-emerald-100 p-2 rounded-lg text-emerald-600">
            <ListChecks class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('helpSelectedTitle') }}</CardTitle>
        </CardHeader>
        <CardContent>
          <p class="text-sm text-ink-600 leading-relaxed">
            {{ t('helpSelectedDesc') }}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-rose-100 p-2 rounded-lg text-rose-600">
            <Wind class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('helpZenTitle') }}</CardTitle>
        </CardHeader>
        <CardContent>
          <p class="text-sm text-ink-600 leading-relaxed">
            {{ t('helpZenDesc') }}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader class="flex flex-row items-center gap-4">
          <div class="bg-indigo-100 p-2 rounded-lg text-indigo-600">
            <BookOpen class="h-6 w-6" />
          </div>
          <CardTitle class="text-lg">{{ t('markdown') }}</CardTitle>
        </CardHeader>
        <CardContent>
          <p class="text-sm text-ink-600 leading-relaxed">
            Markdown summaries support high-performance rendering of <strong>KaTeX</strong> math, 
            <strong>Mermaid</strong> diagrams, and <strong>Markmap</strong> mindmaps. Footnotes can be 
            viewed by hovering over the reference markers.
          </p>
        </CardContent>
      </Card>
    </div>
  </div>
</template>
