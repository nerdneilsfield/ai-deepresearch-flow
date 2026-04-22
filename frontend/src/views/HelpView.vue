<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  Columns2,
  Download,
  Eye,
  FileText,
  Filter,
  Lightbulb,
  ListChecks,
  Route,
  Search,
  Wind,
} from 'lucide-vue-next'

const { t } = useI18n()

const sectionLinks = computed(() => [
  { id: 'quick-start', label: t('helpSectionQuickStart') },
  { id: 'pages', label: t('helpSectionPages') },
  { id: 'workflows', label: t('helpSectionWorkflows') },
  { id: 'reading', label: t('helpSectionReading') },
  { id: 'troubleshooting', label: t('helpSectionTroubleshooting') },
])

const badges = computed(() => [
  t('helpBadgeSearch'),
  t('helpBadgeRead'),
  t('helpBadgeOrganize'),
  t('helpBadgeShare'),
])

const quickStartSteps = computed(() => [
  {
    index: '01',
    title: t('helpStep1Title'),
    description: t('helpStep1Desc'),
  },
  {
    index: '02',
    title: t('helpStep2Title'),
    description: t('helpStep2Desc'),
  },
  {
    index: '03',
    title: t('helpStep3Title'),
    description: t('helpStep3Desc'),
  },
  {
    index: '04',
    title: t('helpStep4Title'),
    description: t('helpStep4Desc'),
  },
])

const pageCards = computed(() => [
  {
    icon: Search,
    tone: 'bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300',
    title: t('helpSearchPageTitle'),
    description: t('helpSearchPageDesc'),
  },
  {
    icon: BarChart3,
    tone: 'bg-violet-100 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300',
    title: t('helpStatsPageTitle'),
    description: t('helpStatsPageDesc'),
  },
  {
    icon: ListChecks,
    tone: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300',
    title: t('helpSelectedPageTitle'),
    description: t('helpSelectedPageDesc'),
  },
  {
    icon: BookOpen,
    tone: 'bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300',
    title: t('helpDetailPageTitle'),
    description: t('helpDetailPageDesc'),
  },
])

const searchModes = computed(() => [
  {
    icon: Search,
    tone: 'bg-sky-100 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300',
    title: t('helpBasicSearchTitle'),
    description: t('helpBasicSearchDesc'),
    bullets: [
      t('helpBasicSearchFeature1'),
      t('helpBasicSearchFeature2'),
      t('helpBasicSearchFeature3'),
    ],
  },
  {
    icon: Eye,
    tone: 'bg-fuchsia-100 text-fuchsia-700 dark:bg-fuchsia-950/40 dark:text-fuchsia-300',
    title: t('helpAdvancedSearchTitle'),
    description: t('helpAdvancedSearchDesc'),
    bullets: [
      t('helpAdvancedSearchFeature1'),
      t('helpAdvancedSearchFeature2'),
      t('helpAdvancedSearchFeature3'),
    ],
  },
])

const workflows = computed(() => [
  {
    title: t('helpWorkflow1Title'),
    steps: [
      t('helpWorkflow1Step1'),
      t('helpWorkflow1Step2'),
      t('helpWorkflow1Step3'),
    ],
  },
  {
    title: t('helpWorkflow2Title'),
    steps: [
      t('helpWorkflow2Step1'),
      t('helpWorkflow2Step2'),
      t('helpWorkflow2Step3'),
    ],
  },
  {
    title: t('helpWorkflow3Title'),
    steps: [
      t('helpWorkflow3Step1'),
      t('helpWorkflow3Step2'),
      t('helpWorkflow3Step3'),
    ],
  },
])

const readingCards = computed(() => [
  {
    icon: FileText,
    tone: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950/40 dark:text-indigo-300',
    title: t('helpReadingMode1Title'),
    description: t('helpReadingMode1Desc'),
  },
  {
    icon: Eye,
    tone: 'bg-cyan-100 text-cyan-700 dark:bg-cyan-950/40 dark:text-cyan-300',
    title: t('helpReadingMode2Title'),
    description: t('helpReadingMode2Desc'),
  },
  {
    icon: Columns2,
    tone: 'bg-rose-100 text-rose-700 dark:bg-rose-950/40 dark:text-rose-300',
    title: t('helpReadingMode3Title'),
    description: t('helpReadingMode3Desc'),
  },
  {
    icon: Download,
    tone: 'bg-teal-100 text-teal-700 dark:bg-teal-950/40 dark:text-teal-300',
    title: t('helpReadingMode4Title'),
    description: t('helpReadingMode4Desc'),
  },
])

const troubleshootingItems = computed(() => [
  {
    title: t('helpTrouble1Title'),
    description: t('helpTrouble1Desc'),
  },
  {
    title: t('helpTrouble2Title'),
    description: t('helpTrouble2Desc'),
  },
  {
    title: t('helpTrouble3Title'),
    description: t('helpTrouble3Desc'),
  },
  {
    title: t('helpTrouble4Title'),
    description: t('helpTrouble4Desc'),
  },
])

const operationsTips = computed(() => [
  t('helpOps1'),
  t('helpOps2'),
  t('helpOps3'),
  t('helpOps4'),
])
</script>

<template>
  <div class="mx-auto max-w-6xl space-y-8 pb-14">
    <section
      data-testid="help-hero"
      class="overflow-hidden rounded-[28px] border border-ink-200 bg-gradient-to-br from-sky-50 via-white to-indigo-50 shadow-card dark:border-ink-700 dark:from-ink-900 dark:via-ink-900 dark:to-ink-800"
    >
      <div class="grid gap-6 px-6 py-8 lg:grid-cols-[1.2fr_0.8fr] lg:px-8 lg:py-10">
        <div class="space-y-5">
          <div class="flex flex-wrap gap-2">
            <Badge
              v-for="badge in badges"
              :key="badge"
              variant="secondary"
              class="rounded-full border border-white/70 bg-white/80 px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-ink-700 dark:border-ink-700 dark:bg-ink-800/90 dark:text-ink-200"
            >
              {{ badge }}
            </Badge>
          </div>

          <div class="space-y-3">
            <h1 class="text-4xl font-bold tracking-tight text-ink-900 dark:text-ink-50">
              {{ t('helpTitle') }}
            </h1>
            <p class="max-w-3xl text-lg leading-8 text-ink-700 dark:text-ink-300">
              {{ t('helpIntro') }}
            </p>
            <p class="max-w-3xl text-sm leading-7 text-ink-500 dark:text-ink-400">
              {{ t('helpSummaryLine') }}
            </p>
          </div>
        </div>

        <Card class="border-0 bg-white/80 shadow-none backdrop-blur dark:bg-ink-900/70">
          <CardHeader class="space-y-2">
            <CardTitle class="flex items-center gap-2 text-lg text-ink-900 dark:text-ink-100">
              <Route class="h-5 w-5 text-accent-600" />
              {{ t('helpQuickStartTitle') }}
            </CardTitle>
            <CardDescription class="text-sm leading-6 text-ink-600 dark:text-ink-400">
              {{ t('helpQuickStartIntro') }}
            </CardDescription>
          </CardHeader>
          <CardContent class="space-y-3">
            <a
              v-for="section in sectionLinks"
              :key="section.id"
              class="group flex items-center justify-between rounded-2xl border border-ink-200 bg-white px-4 py-3 text-sm font-medium text-ink-700 transition hover:border-accent-300 hover:bg-accent-50 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-200 dark:hover:border-accent-700 dark:hover:bg-accent-950/30"
              :href="`#${section.id}`"
            >
              <span>{{ section.label }}</span>
              <ArrowRight class="h-4 w-4 transition group-hover:translate-x-1" />
            </a>
          </CardContent>
        </Card>
      </div>
    </section>

    <section
      id="quick-start"
      data-testid="help-section"
      class="space-y-4"
    >
      <div class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-sky-100 p-2 text-sky-700 dark:bg-sky-950/40 dark:text-sky-300">
            <Search class="h-5 w-5" />
          </div>
          <h2 class="text-2xl font-semibold text-ink-900 dark:text-ink-100">
            {{ t('helpQuickStartTitle') }}
          </h2>
        </div>
        <p class="max-w-4xl text-sm leading-7 text-ink-600 dark:text-ink-400">
          {{ t('helpQuickStartIntro') }}
        </p>
      </div>

      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <Card
          v-for="step in quickStartSteps"
          :key="step.index"
          class="border-ink-200 dark:border-ink-700"
        >
          <CardHeader class="space-y-3">
            <div class="text-xs font-semibold uppercase tracking-[0.24em] text-ink-400">
              {{ step.index }}
            </div>
            <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
              {{ step.title }}
            </CardTitle>
          </CardHeader>
          <CardContent class="text-sm leading-7 text-ink-600 dark:text-ink-400">
            {{ step.description }}
          </CardContent>
        </Card>
      </div>
    </section>

    <section
      id="pages"
      data-testid="help-section"
      class="space-y-4"
    >
      <div class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-violet-100 p-2 text-violet-700 dark:bg-violet-950/40 dark:text-violet-300">
            <Filter class="h-5 w-5" />
          </div>
          <h2 class="text-2xl font-semibold text-ink-900 dark:text-ink-100">
            {{ t('helpPagesTitle') }}
          </h2>
        </div>
        <p class="max-w-4xl text-sm leading-7 text-ink-600 dark:text-ink-400">
          {{ t('helpPagesIntro') }}
        </p>
      </div>

      <Card class="border-ink-200 dark:border-ink-700">
        <CardHeader>
          <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
            {{ t('helpSearchModesTitle') }}
          </CardTitle>
          <CardDescription class="text-sm leading-7 text-ink-600 dark:text-ink-400">
            {{ t('helpSearchModesIntro') }}
          </CardDescription>
        </CardHeader>
        <CardContent class="grid gap-4 md:grid-cols-2">
          <div
            v-for="mode in searchModes"
            :key="mode.title"
            class="rounded-2xl border border-ink-100 bg-ink-50/60 p-4 dark:border-ink-800 dark:bg-ink-900/50"
          >
            <div class="flex items-start gap-3">
              <div :class="['rounded-2xl p-2', mode.tone]">
                <component :is="mode.icon" class="h-5 w-5" />
              </div>
              <div class="space-y-2">
                <div class="text-base font-semibold text-ink-900 dark:text-ink-100">
                  {{ mode.title }}
                </div>
                <div class="text-sm leading-7 text-ink-600 dark:text-ink-400">
                  {{ mode.description }}
                </div>
              </div>
            </div>

            <div class="mt-4 space-y-2">
              <div
                v-for="bullet in mode.bullets"
                :key="bullet"
                class="flex gap-3 text-sm leading-7 text-ink-600 dark:text-ink-400"
              >
                <div class="mt-2 h-2 w-2 shrink-0 rounded-full bg-accent-500" />
                <div>{{ bullet }}</div>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      <div class="grid gap-4 md:grid-cols-2">
        <Card
          v-for="page in pageCards"
          :key="page.title"
          class="border-ink-200 dark:border-ink-700"
        >
          <CardHeader class="flex flex-row items-start gap-4 space-y-0">
            <div :class="['rounded-2xl p-2', page.tone]">
              <component :is="page.icon" class="h-5 w-5" />
            </div>
            <div class="space-y-2">
              <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
                {{ page.title }}
              </CardTitle>
              <CardDescription class="text-sm leading-7 text-ink-600 dark:text-ink-400">
                {{ page.description }}
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      </div>
    </section>

    <section
      id="workflows"
      data-testid="help-section"
      class="space-y-4"
    >
      <div class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-emerald-100 p-2 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
            <ListChecks class="h-5 w-5" />
          </div>
          <h2 class="text-2xl font-semibold text-ink-900 dark:text-ink-100">
            {{ t('helpWorkflowTitle') }}
          </h2>
        </div>
        <p class="max-w-4xl text-sm leading-7 text-ink-600 dark:text-ink-400">
          {{ t('helpWorkflowIntro') }}
        </p>
      </div>

      <div class="grid gap-4 lg:grid-cols-3">
        <Card
          v-for="workflow in workflows"
          :key="workflow.title"
          class="border-ink-200 dark:border-ink-700"
        >
          <CardHeader>
            <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
              {{ workflow.title }}
            </CardTitle>
          </CardHeader>
          <CardContent class="space-y-3">
            <div
              v-for="(step, index) in workflow.steps"
              :key="step"
              class="flex gap-3 text-sm leading-7 text-ink-600 dark:text-ink-400"
            >
              <div class="mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-ink-100 text-[11px] font-semibold text-ink-700 dark:bg-ink-800 dark:text-ink-200">
                {{ index + 1 }}
              </div>
              <div>{{ step }}</div>
            </div>
          </CardContent>
        </Card>
      </div>
    </section>

    <section
      id="reading"
      data-testid="help-section"
      class="space-y-4"
    >
      <div class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-amber-100 p-2 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300">
            <BookOpen class="h-5 w-5" />
          </div>
          <h2 class="text-2xl font-semibold text-ink-900 dark:text-ink-100">
            {{ t('helpReadingTitle') }}
          </h2>
        </div>
        <p class="max-w-4xl text-sm leading-7 text-ink-600 dark:text-ink-400">
          {{ t('helpReadingIntro') }}
        </p>
      </div>

      <div class="grid gap-4 md:grid-cols-2">
        <Card
          v-for="item in readingCards"
          :key="item.title"
          class="border-ink-200 dark:border-ink-700"
        >
          <CardHeader class="flex flex-row items-start gap-4 space-y-0">
            <div :class="['rounded-2xl p-2', item.tone]">
              <component :is="item.icon" class="h-5 w-5" />
            </div>
            <div class="space-y-2">
              <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
                {{ item.title }}
              </CardTitle>
              <CardDescription class="text-sm leading-7 text-ink-600 dark:text-ink-400">
                {{ item.description }}
              </CardDescription>
            </div>
          </CardHeader>
        </Card>
      </div>
    </section>

    <section
      id="troubleshooting"
      data-testid="help-section"
      class="space-y-4"
    >
      <div class="space-y-2">
        <div class="flex items-center gap-3">
          <div class="rounded-2xl bg-slate-100 p-2 text-slate-700 dark:bg-slate-900 dark:text-slate-300">
            <Lightbulb class="h-5 w-5" />
          </div>
          <h2 class="text-2xl font-semibold text-ink-900 dark:text-ink-100">
            {{ t('helpTroubleshootingTitle') }}
          </h2>
        </div>
        <p class="max-w-4xl text-sm leading-7 text-ink-600 dark:text-ink-400">
          {{ t('helpTroubleshootingIntro') }}
        </p>
      </div>

      <div class="grid gap-4 lg:grid-cols-[1.3fr_0.7fr]">
        <Card class="border-ink-200 dark:border-ink-700">
          <CardContent class="space-y-4 pt-6">
            <div
              v-for="item in troubleshootingItems"
              :key="item.title"
              class="rounded-2xl border border-ink-100 bg-ink-50/70 p-4 dark:border-ink-800 dark:bg-ink-900/50"
            >
              <div class="text-sm font-semibold text-ink-900 dark:text-ink-100">
                {{ item.title }}
              </div>
              <div class="mt-2 text-sm leading-7 text-ink-600 dark:text-ink-400">
                {{ item.description }}
              </div>
            </div>
          </CardContent>
        </Card>

        <Card class="border-ink-200 dark:border-ink-700">
          <CardHeader class="flex flex-row items-center gap-3 space-y-0">
            <div class="rounded-2xl bg-ink-100 p-2 text-ink-700 dark:bg-ink-800 dark:text-ink-200">
              <Wind class="h-5 w-5" />
            </div>
            <div>
              <CardTitle class="text-lg text-ink-900 dark:text-ink-100">
                {{ t('helpOpsTitle') }}
              </CardTitle>
              <CardDescription class="text-sm leading-7 text-ink-600 dark:text-ink-400">
                {{ t('helpOpsIntro') }}
              </CardDescription>
            </div>
          </CardHeader>
          <CardContent class="space-y-3">
            <div
              v-for="tip in operationsTips"
              :key="tip"
              class="flex gap-3 text-sm leading-7 text-ink-600 dark:text-ink-400"
            >
              <div class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-accent-500" />
              <div>{{ tip }}</div>
            </div>
          </CardContent>
        </Card>
      </div>
    </section>
  </div>
</template>
