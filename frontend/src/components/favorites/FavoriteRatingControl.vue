<script setup lang="ts">
import { Star } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { Button } from '@/components/ui/button'
import type { FavoriteRating } from '@/types/favorites'

const props = withDefaults(defineProps<{
  isFavorite?: boolean
  rating?: FavoriteRating
}>(), {
  isFavorite: false,
})

const emit = defineEmits<{
  toggleFavorite: []
  setRating: [rating: FavoriteRating]
}>()

const { t } = useI18n()
const ratings: FavoriteRating[] = [1, 2, 3, 4, 5]

function toggleFavorite() {
  emit('toggleFavorite')
}

function setRating(rating: FavoriteRating) {
  emit('setRating', rating)
}
</script>

<template>
  <div class="flex items-center gap-0.5" @click.stop @keydown.stop>
    <Button
      size="icon-sm"
      variant="ghost"
      type="button"
      data-testid="favorite-toggle"
      :aria-label="isFavorite ? t('removeFavorite') : t('addFavorite')"
      :aria-pressed="isFavorite"
      @click="toggleFavorite"
    >
      <Star
        class="h-4 w-4"
        :class="isFavorite ? 'fill-amber-400 text-amber-500' : 'text-muted-foreground'"
      />
    </Button>
    <div
      v-if="isFavorite"
      class="flex items-center gap-0.5"
      role="radiogroup"
      :aria-label="t('favoriteRating')"
    >
      <Button
        v-for="value in ratings"
        :key="value"
        size="icon-sm"
        variant="ghost"
        type="button"
        role="radio"
        :data-testid="`favorite-rating-${value}`"
        :aria-checked="rating === value"
        :aria-label="t('setFavoriteRating', { rating: value })"
        @click="setRating(value)"
      >
        <Star
          class="h-3.5 w-3.5"
          :class="rating && value <= rating ? 'fill-amber-400 text-amber-500' : 'text-muted-foreground/50'"
        />
      </Button>
    </div>
  </div>
</template>
