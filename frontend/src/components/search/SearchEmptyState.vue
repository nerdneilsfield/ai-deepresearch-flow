<script setup lang="ts">
import { useI18n } from 'vue-i18n'

defineProps<{ hints?: string[] }>()
const { t } = useI18n()
</script>

<template>
  <div
    class="rounded-xl border border-dashed border-border bg-card px-4 py-8 text-center text-sm text-muted-foreground dark:border-ink-700 dark:bg-ink-900/80 dark:text-ink-300"
    data-testid="search-empty"
  >
    <div class="font-medium text-foreground dark:text-ink-100">{{ t('noResultsTitle') }}</div>
    <div class="mt-1 flex flex-wrap items-center justify-center gap-1.5">
      <span>{{ hints?.length ? t('noResultsHint') : t('noResultsPlain') }}</span>
      <code v-for="hint in hints" :key="hint" class="rounded bg-muted px-1.5 py-0.5 text-xs dark:bg-ink-800">{{ hint }}</code>
    </div>
  </div>
</template>
