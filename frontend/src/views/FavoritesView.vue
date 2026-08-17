<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Save, Star, Upload } from 'lucide-vue-next'
import { useFavoriteStore } from '@/stores/favorites'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
import { lazySaveAs, lazySnippet } from '@/lib/lazy'
import { readLocalLibraryImportText } from '@/lib/local-library-import'
import { useExpandableSummary } from '@/composables/useExpandableSummary'
import { Button } from '@/components/ui/button'
import SearchResultItem from '@/components/search/SearchResultItem.vue'
import { SearchItemSchema } from '@/types/api'
import { isFavoriteRating, type FavoriteRating, type FavoriteRecord } from '@/types/favorites'
import { isManualSyncTimestamp, MAX_MANUAL_SYNC_FAVORITE_RECORDS } from '@/types/manual-sync'

const { t } = useI18n()
const router = useRouter()
const favorites = useFavoriteStore()
const selection = useSelectionStore()
const ui = useUiStore()
const ratingFilter = ref<'all' | `${FavoriteRating}`>('all')
const fileInput = ref<HTMLInputElement | null>(null)
const listImportMode = ref<'merge' | 'replace'>('merge')
const listShowModePopover = ref(false)
const { expanded, expandedMarkdown, expandedLoading, toggleSummary } = useExpandableSummary()
const snippetRenderer = ref<(value: string) => string>((value) => value)

const visibleFavorites = computed(() => {
  if (ratingFilter.value === 'all') return favorites.sortedItems
  const rating = Number(ratingFilter.value) as FavoriteRating
  return favorites.sortedItems.filter((favorite) => favorite.rating === rating)
})

async function saveFavorites() {
  const saveAs = await lazySaveAs()
  const data = JSON.stringify({
    type: 'paperdb-favorites',
    version: 1,
    items: favorites.items,
  }, null, 2)
  const blob = new Blob([data], { type: 'application/json;charset=utf-8' })
  saveAs(blob, `paperdb_favorites_${Date.now()}.json`)
}

function triggerLoadFavorites(mode: 'merge' | 'replace') {
  listImportMode.value = mode
  listShowModePopover.value = false
  fileInput.value?.click()
}

function favoriteEntries(payload: unknown): unknown[] {
  let entries: unknown[]
  if (Array.isArray(payload)) {
    entries = payload
  } else if (
    payload &&
    typeof payload === 'object' &&
    (payload as { type?: unknown }).type === 'paperdb-favorites' &&
    Array.isArray((payload as { items?: unknown }).items)
  ) {
    entries = (payload as { items: unknown[] }).items
  } else {
    throw new Error('Invalid favorites list format')
  }
  if (entries.length > MAX_MANUAL_SYNC_FAVORITE_RECORDS) throw new Error('Favorite list exceeds the import limit')
  return entries
}

function toFavoriteRecord(value: unknown): FavoriteRecord | null {
  if (!value || typeof value !== 'object') return null
  const entry = value as Partial<FavoriteRecord>
  const paper = SearchItemSchema.safeParse(entry.paper)
  if (!paper.success || !isFavoriteRating(entry.rating)) return null
  const now = Date.now()
  const createdAt = isManualSyncTimestamp(entry.createdAt) ? entry.createdAt : now
  const updatedAt = isManualSyncTimestamp(entry.updatedAt) ? entry.updatedAt : createdAt
  return {
    paper: paper.data,
    rating: entry.rating,
    createdAt,
    updatedAt,
  }
}

async function handleFavoriteFileLoad(event: Event) {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return

  try {
    const entries = favoriteEntries(JSON.parse(await readLocalLibraryImportText(file)))
    const records = entries
      .map(toFavoriteRecord)
      .filter((record): record is FavoriteRecord => record !== null)
    if (entries.length > 0 && records.length === 0) throw new Error('No valid favorites')
    const imported = listImportMode.value === 'replace'
      ? await favorites.replace(records)
      : await favorites.merge(records)
    ui.pushToast(t('listImportCompleted', { count: imported }), 'success')
  } catch (error) {
    console.error(error)
    ui.pushToast(t('listImportFailed'), 'error')
  } finally {
    target.value = ''
  }
}

onMounted(async () => {
  await favorites.init()
  const renderSnippet = await lazySnippet()
  // @ts-ignore
  snippetRenderer.value = (value: string) => String(renderSnippet(value))
})
</script>

<template>
  <div class="space-y-6">
    <div class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-bold text-foreground dark:text-ink-100">{{ t('favoritesTitle') }}</h1>
        <p class="text-sm text-muted-foreground dark:text-ink-400">
          {{ t('favoritesDescription', { count: favorites.count }) }}
        </p>
      </div>
      <div class="flex flex-wrap items-center justify-end gap-2">
        <label class="flex items-center gap-2 text-sm text-muted-foreground dark:text-ink-300">
          <span>{{ t('favoritesFilter') }}</span>
          <select
            v-model="ratingFilter"
            data-testid="favorites-rating-filter"
            class="h-9 rounded-md border border-input bg-background px-2 text-sm text-foreground shadow-sm outline-none focus:ring-1 focus:ring-ring dark:border-ink-700 dark:bg-ink-900"
          >
            <option value="all">{{ t('allRatings') }}</option>
            <option v-for="rating in [5, 4, 3, 2, 1]" :key="rating" :value="String(rating)">
              {{ t('favoriteRatingValue', { rating }) }}
            </option>
          </select>
        </label>
        <input
          ref="fileInput"
          type="file"
          accept=".json,application/json"
          class="hidden"
          @change="handleFavoriteFileLoad"
        />
        <Button variant="outline" size="sm" :disabled="favorites.count === 0" @click="saveFavorites">
          <Save class="mr-2 h-4 w-4" /> {{ t('saveList') }}
        </Button>
        <div class="relative">
          <Button variant="outline" size="sm" @click="listShowModePopover = !listShowModePopover">
            <Upload class="mr-2 h-4 w-4" /> {{ t('loadList') }}
          </Button>
          <div
            v-if="listShowModePopover"
            class="absolute right-0 top-full z-10 mt-1 w-48 rounded-md border border-border/60 bg-popover p-1 text-popover-foreground shadow-lg dark:border-ink-700 dark:bg-ink-900"
          >
            <button
              class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-muted dark:hover:bg-ink-800"
              @click="triggerLoadFavorites('merge')"
            >
              {{ t('listImportMerge') }}
            </button>
            <button
              class="w-full rounded px-3 py-1.5 text-left text-sm hover:bg-muted dark:hover:bg-ink-800"
              @click="triggerLoadFavorites('replace')"
            >
              {{ t('listImportReplace') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="favorites.count === 0"
      class="flex min-h-[300px] flex-col items-center justify-center rounded-xl border-2 border-dashed border-ink-200 bg-ink-50 p-8 text-center text-ink-500 dark:border-ink-700 dark:bg-ink-900/50 dark:text-ink-300"
    >
      <Star class="mb-4 h-12 w-12 text-amber-400" />
      <h2 class="text-lg font-medium text-ink-700 dark:text-ink-100">{{ t('noFavorites') }}</h2>
      <p class="mt-2 max-w-sm">{{ t('noFavoritesDesc') }}</p>
      <Button class="mt-5" variant="outline" @click="router.push('/')">{{ t('browsePapers') }}</Button>
    </div>

    <div
      v-else-if="visibleFavorites.length === 0"
      class="rounded-xl border border-dashed border-border bg-card p-8 text-center text-sm text-muted-foreground dark:border-ink-700 dark:bg-ink-900/80 dark:text-ink-300"
    >
      {{ t('noFavoritesForRating') }}
    </div>

    <div v-else class="space-y-3">
      <SearchResultItem
        v-for="(favorite, index) in visibleFavorites"
        :key="favorite.paper.paper_id"
        :item="favorite.paper"
        :display-index="index + 1"
        :is-selected="selection.selectedIds.has(favorite.paper.paper_id)"
        :selection-full="selection.isFull"
        :is-favorite="true"
        :favorite-rating="favorite.rating"
        :expanded="expanded[favorite.paper.paper_id]"
        :expanded-markdown="expandedMarkdown[favorite.paper.paper_id]"
        :expanded-loading="expandedLoading[favorite.paper.paper_id]"
        :snippet-renderer="snippetRenderer"
        @toggle-select="selection.toggle(favorite.paper)"
        @toggle-favorite="favorites.remove(favorite.paper.paper_id)"
        @set-favorite-rating="favorites.setRating(favorite.paper.paper_id, $event)"
        @toggle-summary="toggleSummary(favorite.paper)"
      />
    </div>
  </div>
</template>
