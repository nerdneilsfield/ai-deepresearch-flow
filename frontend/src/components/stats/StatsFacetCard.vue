<script setup lang="ts">
import { computed, ref } from 'vue'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ChevronDown, ChevronUp } from 'lucide-vue-next'
import VChart from 'vue-echarts'
import { useI18n } from 'vue-i18n'
import { useChartTheme } from '@/composables/useChartTheme'

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
const chartTheme = useChartTheme()
const expanded = ref(false)

const visibleItems = computed(() =>
  expanded.value ? props.items : props.items.slice(0, props.defaultVisibleCount)
)

const pieOption = computed(() => {
  const theme = chartTheme.value
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
    title: { text: props.title, left: 'center', show: false, textStyle: { fontSize: 12, color: theme.foreground } },
    tooltip: { trigger: 'item', ...theme.tooltip },
    legend: { bottom: 0, type: 'scroll', textStyle: { color: theme.axis, fontSize: 11 } },
    series: [
      {
        type: 'pie',
        radius: ['35%', '60%'],
        center: ['50%', '45%'],
        data,
        label: { color: theme.axis, fontSize: 11 },
        itemStyle: { borderRadius: 4 },
        color: props.chartColors || theme.paletteLong,
      },
    ],
  }
})
</script>

<template>
  <Card class="space-y-0 shadow-card">
    <CardHeader class="flex flex-row items-center justify-between">
      <CardTitle class="font-display-serif text-sm font-semibold">{{ title }}</CardTitle>
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
      <div v-if="showChart && items.length" class="my-4 h-px bg-border"></div>
      <table class="w-full text-sm">
        <thead class="text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th class="py-2 text-left">{{ title }}</th>
            <th class="py-2 text-right">{{ t('count') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in visibleItems" :key="item.value" class="border-t border-border">
            <td class="py-2">
              <button type="button" class="text-foreground/80 hover:text-primary" @click="emit('facetClick', item.value)">
                {{ item.value }}
              </button>
            </td>
            <td class="py-2 text-right text-muted-foreground">{{ item.paper_count }}</td>
          </tr>
        </tbody>
      </table>
    </CardContent>
  </Card>
</template>
