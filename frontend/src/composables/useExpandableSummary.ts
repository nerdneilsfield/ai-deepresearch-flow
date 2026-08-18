import { ref } from 'vue'
import { getSummaryPayloadCached } from '@/lib/api'
import { normalizeSummaryText } from '@/lib/summary-text'
import { useUiStore } from '@/stores/ui'
import type { SearchResponse } from '@/types/api'

type SearchItem = SearchResponse['items'][number]

export function useExpandableSummary() {
  const ui = useUiStore()
  const expanded = ref<Record<string, boolean>>({})
  const expandedMarkdown = ref<Record<string, string>>({})
  const expandedLoading = ref<Record<string, boolean>>({})
  const expandedSummaryUrls = ref<Record<string, string>>({})

  async function toggleSummary(item: SearchItem) {
    const id = item.paper_id
    if (expanded.value[id]) {
      expanded.value = { ...expanded.value, [id]: false }
      return
    }

    if ((!expandedMarkdown.value[id] || expandedSummaryUrls.value[id] !== item.summary_url) && item.summary_url) {
      expandedLoading.value = { ...expandedLoading.value, [id]: true }
      try {
        const template = item.preferred_summary_template || 'default'
        const data = await getSummaryPayloadCached(item.paper_id, template, item.summary_url) as { summary?: string }
        expandedMarkdown.value = {
          ...expandedMarkdown.value,
          [id]: normalizeSummaryText(data.summary),
        }
        expandedSummaryUrls.value = { ...expandedSummaryUrls.value, [id]: item.summary_url }
      } catch {
        ui.pushToast('Failed to load summary', 'error')
      } finally {
        expandedLoading.value = { ...expandedLoading.value, [id]: false }
      }
    }
    expanded.value = { ...expanded.value, [id]: true }
  }

  return { expanded, expandedMarkdown, expandedLoading, toggleSummary }
}
