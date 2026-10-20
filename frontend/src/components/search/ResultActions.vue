<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Square, SquareCheck } from 'lucide-vue-next'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import FavoriteRatingControl from '@/components/favorites/FavoriteRatingControl.vue'
import type { FavoriteRating } from '@/types/favorites'

const props = withDefaults(defineProps<{
  isSelected?: boolean
  selectionFull?: boolean
  isFavorite?: boolean
  favoriteRating?: FavoriteRating
  displayIndex?: number
  selectTestId?: string
}>(), {
  isSelected: false,
  selectionFull: false,
  isFavorite: false,
  selectTestId: 'result-select',
})

const emit = defineEmits<{
  toggleSelect: []
  toggleFavorite: []
  setFavoriteRating: [rating: FavoriteRating]
}>()

const { t } = useI18n()
const selectLabel = computed(() => (props.isSelected ? t('selected_btn') : t('select')))
const selectTip = computed(() =>
  props.selectionFull && !props.isSelected ? t('selectionLimitReached') : selectLabel.value,
)
</script>

<template>
  <div class="flex items-center gap-1" @click.stop @keydown.stop>
    <Badge v-if="displayIndex !== undefined" variant="slate" class="px-1.5 py-0 text-[11px]">#{{ displayIndex }}</Badge>
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger as-child>
          <Button
            size="icon-sm"
            variant="ghost"
            type="button"
            :data-testid="selectTestId"
            :aria-label="selectLabel"
            :aria-pressed="isSelected"
            @click="emit('toggleSelect')"
          >
            <SquareCheck v-if="isSelected" class="h-4 w-4 text-primary" />
            <Square v-else class="h-4 w-4 text-muted-foreground" />
            <span class="sr-only">{{ selectLabel }}</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>{{ selectTip }}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
    <FavoriteRatingControl
      :is-favorite="isFavorite"
      :rating="favoriteRating"
      @toggle-favorite="emit('toggleFavorite')"
      @set-rating="emit('setFavoriteRating', $event)"
    />
  </div>
</template>
