<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { getStats, type StatsResponse } from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { BarChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent, LegendComponent, TitleComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useQuery } from '@tanstack/vue-query'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'
import StatsFacetCard from '@/components/stats/StatsFacetCard.vue'
import { useChartTheme } from '@/composables/useChartTheme'

const router = useRouter()
const { t } = useI18n()
const chartTheme = useChartTheme()
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

const yearOption = computed(() => {
  const theme = chartTheme.value
  return {
    animation: false,
    grid: { left: 48, right: 16, top: 16, bottom: 40 },
    color: [theme.primary],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...theme.tooltip },
    xAxis: {
      type: 'category',
      data: sortedYears.value.map((item) => String(item.value)),
      axisLabel: { color: theme.axis },
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: theme.axis },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name: t('pubYear'),
        type: 'bar',
        data: sortedYears.value.map((item) => item.paper_count),
        barWidth: '60%',
        itemStyle: { color: theme.primary, borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})

const monthOption = computed(() => {
  const theme = chartTheme.value
  return {
    animation: false,
    grid: { left: 48, right: 16, top: 16, bottom: 40 },
    color: [theme.primary],
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, ...theme.tooltip },
    xAxis: {
      type: 'category',
      data: sortedMonths.value.map((item) => String(item.value)),
      axisLabel: { color: theme.axis },
      axisLine: { lineStyle: { color: theme.grid } },
      axisTick: { alignWithLabel: true },
    },
    yAxis: {
      type: 'value',
      axisLabel: { color: theme.axis },
      splitLine: { lineStyle: { color: theme.grid } },
    },
    series: [
      {
        name: t('pubMonth'),
        type: 'bar',
        data: sortedMonths.value.map((item) => item.paper_count),
        barWidth: '60%',
        itemStyle: { color: theme.primary, borderRadius: [4, 4, 0, 0] },
      },
    ],
  }
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-center justify-between gap-3">
      <div>
        <div class="font-display-serif text-lg font-semibold text-foreground">{{ t('stats') }}</div>
        <div class="text-xs text-muted-foreground">{{ t('chartsNote') }}</div>
      </div>
      <Button variant="outline" @click="router.push('/')">{{ t('backToSearch') }}</Button>
    </div>

    <div v-if="loading" class="space-y-4">
      <Skeleton class="h-72 rounded-xl" />
      <Skeleton class="h-72 rounded-xl" />
    </div>
    <div v-else-if="error" class="rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-sm text-destructive">
      {{ error }}
    </div>

    <div v-if="stats" class="space-y-6">
      <Card class="space-y-0">
        <CardHeader>
          <CardTitle class="font-display-serif text-sm font-semibold">{{ t('pubYear') }}</CardTitle>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart class="h-72 w-full" :option="yearOption" autoresize />
        </CardContent>
      </Card>

      <Card class="space-y-0">
        <CardHeader>
          <CardTitle class="font-display-serif text-sm font-semibold">{{ t('pubMonth') }}</CardTitle>
        </CardHeader>
        <CardContent class="pt-0">
          <VChart class="h-72 w-full" :option="monthOption" autoresize />
        </CardContent>
      </Card>

      <StatsFacetCard :title="t('keywords')" :items="stats.keywords" @facet-click="(v) => goFacet('keywords', v)" />
      <StatsFacetCard :title="t('authors')" :items="stats.authors" @facet-click="(v) => goFacet('authors', v)" />
      <StatsFacetCard :title="t('venues')" :items="stats.venues" @facet-click="(v) => goFacet('venues', v)" />
      <StatsFacetCard :title="t('institutions')" :items="stats.institutions" @facet-click="(v) => goFacet('institutions', v)" />
      <StatsFacetCard :title="t('years')" :items="stats.years" :show-chart="false" @facet-click="(v) => goFacet('years', v)" />
      <StatsFacetCard :title="t('months')" :items="stats.months" :show-chart="false" @facet-click="(v) => goFacet('months', v)" />
    </div>
  </div>
</template>
