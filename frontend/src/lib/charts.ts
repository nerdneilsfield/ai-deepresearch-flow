export type ChartInstance = import('echarts').EChartsType

type BarChartOptions = {
  title: string
  labels: string[]
  values: number[]
  color?: string
}

let echartsPromise: Promise<typeof import('echarts')> | null = null

async function loadEcharts() {
  if (!echartsPromise) {
    echartsPromise = import('echarts')
  }
  return echartsPromise
}

export async function renderBarChart(el: HTMLElement, options: BarChartOptions): Promise<ChartInstance> {
  const echarts = await loadEcharts()
  const chart = echarts.getInstanceByDom(el) ?? echarts.init(el)
  chart.setOption({
    animation: false,
    grid: { left: 48, right: 16, top: 16, bottom: 40 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: {
      type: 'category',
      data: options.labels,
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
        name: options.title,
        type: 'bar',
        data: options.values,
        barWidth: '60%',
        itemStyle: { color: options.color || '#4f6ed6', borderRadius: [4, 4, 0, 0] },
      },
    ],
  })
  return chart
}

export function setupChartResize(chart: ChartInstance, el: HTMLElement) {
  const observer = new ResizeObserver(() => chart.resize())
  observer.observe(el)
  return () => observer.disconnect()
}
