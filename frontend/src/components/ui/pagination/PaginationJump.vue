<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

const props = withDefaults(defineProps<{
  modelValue: number
  totalPages: number
  disabled?: boolean
}>(), {
  disabled: false,
})

const emit = defineEmits<{
  (event: 'update:modelValue', page: number): void
}>()

const { t } = useI18n()
const pageInput = ref(String(props.modelValue))

const maxPage = computed(() => {
  const total = Number(props.totalPages)
  return Number.isFinite(total) ? Math.max(1, Math.floor(total)) : 1
})
const controlsDisabled = computed(() => props.disabled || maxPage.value <= 1)

watch(
  () => props.modelValue,
  (page) => {
    pageInput.value = String(page)
  },
)

function jumpToPage() {
  if (controlsDisabled.value) return

  const raw = String(pageInput.value).trim()
  const requested = Number(raw)
  if (!raw || !Number.isInteger(requested)) {
    pageInput.value = String(props.modelValue)
    return
  }

  const nextPage = Math.min(maxPage.value, Math.max(1, requested))
  pageInput.value = String(nextPage)
  if (nextPage !== props.modelValue) emit('update:modelValue', nextPage)
}
</script>

<template>
  <form class="flex items-center gap-1" @submit.prevent="jumpToPage">
    <span class="hidden text-xs text-ink-500 sm:inline">{{ t('jumpToPage') }}</span>
    <Input
      v-model="pageInput"
      data-testid="pagination-jump-input"
      class="h-8 w-16 text-center"
      type="number"
      min="1"
      :max="maxPage"
      step="1"
      inputmode="numeric"
      :disabled="controlsDisabled"
      :aria-label="t('jumpToPage')"
    />
    <Button
      data-testid="pagination-jump-submit"
      type="submit"
      variant="ghost"
      size="sm"
      :disabled="controlsDisabled"
    >
      {{ t('go') }}
    </Button>
  </form>
</template>
