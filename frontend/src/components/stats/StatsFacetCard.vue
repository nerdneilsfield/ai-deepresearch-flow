<script setup lang="ts">
import { computed, ref } from 'vue'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import VChart from 'vue-echarts'
import { useI18n } from 'vue-i18n'

const props = withDefaults(defineProps<{
  title: string
  items: Array<{ value: string; paper_count: number }>
  showChart?: boolean
  chartColors?: string[]
  defaultVisibleCount?: number
}>(), {
  showChart: true,
  defaultVisibleCount: 10,
})

const emit = defineEmits<{
  facetClick: [value: string]
}>()

const { t } = useI18n()
const expanded = ref(false)

const DEFAULT_PIE_COLORS = [
  '#3b82f6', '#0ea5e9', '#06b6d4', '#14b8a6', '#10b981',
  '#84cc16', '#f59e0b', '#f97316', '#ef4444', '#8b5cf6', '#a855f7',
]

const visibleItems = computed(() =>
  expanded.value ? props.items : props.items.slice(0, props.defaultVisibleCount)
)

const pieOption = computed(() => {
  const sorted = [...props.items].sort((a, b) => b.paper_count - a.paper_count)
  const top = sorted.slice(0, 10)
  const others = sorted.slice(10)
  const otherCount = others.reduce((sum, item) => sum + item.paper_count, 0)
  const data = top.map((item) => ({ name: String(item.value), value: item.paper_count }))
  if (otherCount > 0) {
    data.push({ name: 'Others', value: otherCount })
  }
  return {
    animation: false,
    title: { text: props.title, left: 'center', textStyle: { fontSize: 12, color: '#334155' } },
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
        color: props.chartColors || DEFAULT_PIE_COLORS,
      },
    ],
  }
})
</script>

<template>
  <Card class="space-y-0 shadow-card">
    <CardHeader class="flex flex-row items-center justify-between">
      <CardTitle class="text-sm">{{ title }}</CardTitle>
      <Button
        size="icon-sm"
        variant="outline"
        :aria-label="expanded ? 'Collapse' : 'Expand'"
        @click="expanded = !expanded"
      >
        <ChevronUp v-if="expanded" />
        <ChevronDown v-else />
      </Button>
    </CardHeader>
    <CardContent class="pt-0">
      <VChart
        v-if="showChart && items.length"
        class="h-64 w-full"
        :option="pieOption"
        autoresize
      />
      <div v-if="showChart && items.length" class="my-4 h-px bg-ink-100"></div>
      <table class="w-full text-sm">
        <thead class="text-xs uppercase text-ink-400">
          <tr>
            <th class="py-2 text-left">{{ title }}</th>
            <th class="py-2 text-right">{{ t('count') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in visibleItems" :key="item.value" class="border-t border-ink-100">
            <td class="py-2">
              <button type="button" class="text-ink-700 hover:text-accent-600" @click="emit('facetClick', item.value)">
                {{ item.value }}
              </button>
            </td>
            <td class="py-2 text-right text-ink-500">{{ item.paper_count }}</td>
          </tr>
        </tbody>
      </table>
    </CardContent>
  </Card>
</template>
