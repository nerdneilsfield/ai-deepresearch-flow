<script setup lang="ts">
import { reactive } from 'vue'
import { useI18n } from 'vue-i18n'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { buildExpression, type AdvancedFormValues } from '@/lib/search-expression'

const emit = defineEmits<{ apply: [expression: string]; cancel: [] }>()
const { t } = useI18n()

const form = reactive<AdvancedFormValues>({
  allWords: '',
  phrase: '',
  anyWords: '',
  withoutWords: '',
  author: '',
  venue: '',
  yearFrom: '',
  yearTo: '',
})

const textFields = [
  { key: 'allWords', label: 'advAllWords' },
  { key: 'phrase', label: 'advPhrase' },
  { key: 'anyWords', label: 'advAnyWords' },
  { key: 'withoutWords', label: 'advWithout' },
  { key: 'author', label: 'advAuthor' },
  { key: 'venue', label: 'advVenue' },
] as const

function submit() {
  emit('apply', buildExpression(form))
}
</script>

<template>
  <form
    class="space-y-3 rounded-xl border border-border/60 bg-muted/30 p-4 dark:border-ink-700 dark:bg-ink-900/60"
    data-testid="search-advanced-form"
    @submit.prevent="submit"
  >
    <div class="grid gap-3 sm:grid-cols-2">
      <label v-for="field in textFields" :key="field.key" class="space-y-1 text-xs font-medium text-muted-foreground">
        <span>{{ t(field.label) }}</span>
        <Input v-model="form[field.key]" :data-testid="`adv-${field.key}`" class="text-foreground" />
      </label>
      <div class="grid grid-cols-2 gap-3 sm:col-span-2">
        <label class="space-y-1 text-xs font-medium text-muted-foreground">
          <span>{{ t('advYearFrom') }}</span>
          <Input v-model="form.yearFrom" inputmode="numeric" maxlength="4" placeholder="2017" data-testid="adv-yearFrom" class="text-foreground" />
        </label>
        <label class="space-y-1 text-xs font-medium text-muted-foreground">
          <span>{{ t('advYearTo') }}</span>
          <Input v-model="form.yearTo" inputmode="numeric" maxlength="4" placeholder="2024" data-testid="adv-yearTo" class="text-foreground" />
        </label>
      </div>
    </div>
    <div class="flex justify-end gap-2">
      <Button type="button" variant="ghost" size="sm" data-testid="adv-cancel" @click="emit('cancel')">{{ t('advClose') }}</Button>
      <Button type="submit" size="sm" data-testid="adv-submit">{{ t('search') }}</Button>
    </div>
  </form>
</template>
