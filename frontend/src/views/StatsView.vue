<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getStats, type StatsResponse } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import { useQuery } from '@tanstack/vue-query'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'

const router = useRouter()
const { t } = useI18n()
const statsQuery = useQuery({
  queryKey: ['stats'],
  queryFn: () => getStats(),
  staleTime: QUERY_CACHE_POLICY.stats.staleTime,
  gcTime: QUERY_CACHE_POLICY.stats.gcTime,
})
const stats = computed(() => statsQuery.data.value ?? null)
const loading = computed(() => statsQuery.isFetching.value)
const error = computed(() => (statsQuery.error.value ? 'Failed to load stats.' : ''))

use([GridComponent, TooltipComponent, LegendComponent, TitleComponent, BarChart, PieChart, CanvasRenderer])

const sortedYears = computed(() => {
  const items = stats.value?.years ? [...stats.value.years] : []
  return items.sort((a, b) => Number(a.value) - Number(b.value))
})

const monthOrder = ['01','02','03','04','05','06','07','08','09','10','11','12','Unknown']
const sortedMonths = computed(() => {
  const items = stats.value?.months ? [...stats.value.months] : []
  const map = new Map(items.map((item) => [String(item.value), item]))
  return monthOrder
    .map((key) => map.get(key))
    .filter(Boolean) as StatsResponse['months']
})

function goFacet(facet: string, value: string) {
  router.push(`/facet/${facet}/${encodeURIComponent(value)}`)
}

const expanded = ref<Record<string, boolean>>({})

function toggleExpanded(key: string) {
  expanded.value = { ...expanded.value, [key]: !expanded.value[key] }
}

function sliceItems(list: Array<{ value: string; paper_count: number }>, key: string, count = 10) {
  if (expanded.value[key]) return list
  return list.slice(0, count)
}

const chartColor = '#3b82f6' // Blue-500
const pieColors = [
  '#3b82f6', // Blue
  '#10b981', // Emerald
  '#f59e0b', // Amber
  '#f43f5e', // Rose
  '#8b5cf6', // Violet
  '#06b6d4', // Cyan
  '#6366f1', // Indigo
  '#ec4899', // Pink
  '#84cc16', // Lime
  '#14b8a6', // Teal
  '#d946ef', // Fuchsia
]

function buildPieOption(list: Array<{ value: string; paper_count: number }>, title: string) {
  const sorted = [...list].sort((a, b) => b.paper_count - a.paper_count)
  const top = sorted.slice(0, 10)
  const others = sorted.slice(10)
  const otherCount = others.reduce((sum, item) => sum + item.paper_count, 0)
  const data = top.map((item) => ({ name: String(item.value), value: item.paper_count }))
  if (otherCount > 0) {
    data.push({ name: 'Others', value: otherCount })
  }
  return {
    animation: false,
    title: { text: title, left: 'center', textStyle: { fontSize: 12, color: '#334155' } },
    tooltip: { trigger: 'item' },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: '#64748b', fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['35%', '60%'],
        center: ['50%', '45%'],
        data,
        label: { color: '#475569', fontSize: 11 },
        itemStyle: { borderRadius: 4 },
        color: pieColors,
      },
    ],
  }
}

const yearOption = computed(() => ({
  animation: false,
  grid: { left: 48, right: 16, top: 16, bottom: 40 },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  xAxis: {
    type: 'category',
    data: sortedYears.value.map((item) => String(item.value)),
    axisLabel: { color: '#475569' },
    axisTick: { alignWithLabel: true },
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#64748b' },
    splitLine: { lineStyle: { color: '#e2e8f0' } },
  },
  series: [
    {
      name: t('pubYear'),
      type: 'bar',
      data: sortedYears.value.map((item) => item.paper_count),
      barWidth: '60%',
      itemStyle: { color: chartColor, borderRadius: [4, 4, 0, 0] },
    },
  ],
}))

const monthOption = computed(() => ({
  animation: false,
  grid: { left: 48, right: 16, top: 16, bottom: 40 },
  tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
  xAxis: {
    type: 'category',
    data: sortedMonths.value.map((item) => String(item.value)),
    axisLabel: { color: '#475569' },
    axisTick: { alignWithLabel: true },
  },
  yAxis: {
    type: 'value',
    axisLabel: { color: '#64748b' },
    splitLine: { lineStyle: { color: '#e2e8f0' } },
  },
  series: [
    {
      name: t('pubMonth'),
      type: 'bar',
      data: sortedMonths.value.map((item) => item.paper_count),
      barWidth: '60%',
      itemStyle: { color: chartColor, borderRadius: [4, 4, 0, 0] },
    },
  ],
}))

</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div class="text-sm font-semibold text-ink-900">{{ t('stats') }}</div>
        <div class="text-xs text-ink-500">{{ t('chartsNote') }}</div>
      </div>
      <Button variant="outline" @click="router.push('/')">{{ t('backToSearch') }}</Button>
    </div>

    <div v-if="loading" class="rounded-xl border border-ink-100 bg-white p-6 text-sm text-ink-500">
      {{ t('loading') }}
    </div>
    <div v-else-if="error" class="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-600">
      {{ error }}
    </div>

    <div v-if="stats" class="space-y-6">
      <Card class="space-y-0">
        <CardHeader>
          <CardTitle class="text-sm">{{ t('pubYear') }}</CardTitle>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart class="h-72 w-full" :option="yearOption" autoresize />
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader>
          <CardTitle class="text-sm">{{ t('pubMonth') }}</CardTitle>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart class="h-72 w-full" :option="monthOption" autoresize />
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('keywords') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.keywords ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('keywords')"
          >
            <ChevronUp v-if="expanded.keywords" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart
            v-if="stats.keywords.length"
            class="h-64 w-full"
            :option="buildPieOption(stats.keywords, t('keywords'))"
            autoresize
          />
          <div class="my-4 h-px bg-ink-100"></div>
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('keywords') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.keywords, 'keywords')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('keywords', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('authors') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.authors ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('authors')"
          >
            <ChevronUp v-if="expanded.authors" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart
            v-if="stats.authors.length"
            class="h-64 w-full"
            :option="buildPieOption(stats.authors, t('authors'))"
            autoresize
          />
          <div class="my-4 h-px bg-ink-100"></div>
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('authors') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.authors, 'authors')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('authors', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('venues') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.venues ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('venues')"
          >
            <ChevronUp v-if="expanded.venues" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart
            v-if="stats.venues.length"
            class="h-64 w-full"
            :option="buildPieOption(stats.venues, t('venues'))"
            autoresize
          />
          <div class="my-4 h-px bg-ink-100"></div>
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('venues') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.venues, 'venues')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('venues', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('institutions') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.institutions ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('institutions')"
          >
            <ChevronUp v-if="expanded.institutions" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart
            v-if="stats.institutions.length"
            class="h-64 w-full"
            :option="buildPieOption(stats.institutions, t('institutions'))"
            autoresize
          />
          <div class="my-4 h-px bg-ink-100"></div>
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('institutions') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.institutions, 'institutions')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('institutions', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('years') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.years ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('years')"
          >
            <ChevronUp v-if="expanded.years" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('years') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.years, 'years')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('years', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader class="flex flex-row items-center justify-between">
          <CardTitle class="text-sm">{{ t('months') }}</CardTitle>
          <Button
            size="icon-sm"
            variant="outline"
            :aria-label="expanded.months ? 'Collapse' : 'Expand'"
            @click="toggleExpanded('months')"
          >
            <ChevronUp v-if="expanded.months" />
            <ChevronDown v-else />
          </Button>
        </CardHeader>
        <CardContent class="pt-0">
          <table class="w-full text-sm">
            <thead class="text-xs uppercase text-ink-400">
              <tr>
                <th class="py-2 text-left">{{ t('months') }}</th>
                <th class="py-2 text-right">{{ t('count') }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in sliceItems(stats.months, 'months')" :key="item.value" class="border-t border-ink-100">
                <td class="py-2">
                  <button type="button" class="text-ink-700 hover:text-accent-600" @click="goFacet('months', item.value)">
                    {{ item.value }}
                  </button>
                </td>
                <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
              </tr>
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  </div>
</template>
