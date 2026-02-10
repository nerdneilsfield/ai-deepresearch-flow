<script setup lang="ts">
import { computed } from 'vue'
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
import { useQuery } from '@tanstack/vue-query'
import { QUERY_CACHE_POLICY } from '@/lib/query-client'
import StatsFacetCard from '@/components/stats/StatsFacetCard.vue'

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

const chartColor = '#3b82f6'

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

      <StatsFacetCard :title="t('keywords')" :items="stats.keywords" @facet-click="(v) => goFacet('keywords', v)" />
      <StatsFacetCard :title="t('authors')" :items="stats.authors" @facet-click="(v) => goFacet('authors', v)" />
      <StatsFacetCard :title="t('venues')" :items="stats.venues" @facet-click="(v) => goFacet('venues', v)" />
      <StatsFacetCard :title="t('institutions')" :items="stats.institutions" @facet-click="(v) => goFacet('institutions', v)" />
      <StatsFacetCard :title="t('years')" :items="stats.years" :show-chart="false" @facet-click="(v) => goFacet('years', v)" />
      <StatsFacetCard :title="t('months')" :items="stats.months" :show-chart="false" @facet-click="(v) => goFacet('months', v)" />
    </div>
  </div>
</template>
