<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { X } from 'lucide-vue-next'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import type { ExprToken } from '@/lib/search-expression'

const props = defineProps<{
  tokens: ExprToken[]
  mode: 'keyword' | 'semantic'
  /** `start` offsets of tokens that the semantic endpoint ignores. */
  ignored?: Set<number>
}>()

const emit = defineEmits<{ remove: [token: ExprToken] }>()
const { t } = useI18n()

const FIELD_KEYS: Record<string, string> = {
  author: 'fieldAuthor',
  venue: 'fieldVenue',
  keyword: 'fieldKeyword',
  tag: 'fieldTag',
  institution: 'fieldInstitution',
  title: 'fieldTitle',
  year: 'fieldYear',
}
const EXACT_FIELDS = new Set(['author', 'keyword', 'tag'])

const chips = computed(() =>
  props.tokens.map((token) => {
    const field = token.field ? t(FIELD_KEYS[token.field] ?? token.field) : ''
    const body = field ? `${field}: ${token.value}` : `"${token.value}"`
    const label = token.negated ? `${t('chipExclude')} ${body}` : body
    const semantic = props.mode === 'semantic'
    const ignored = semantic && (props.ignored?.has(token.start) ?? false)
    const note = token.invalid
      ? t('chipInvalidYear')
      : ignored
        ? t('chipIgnoredSemantic')
        : semantic && token.field && EXACT_FIELDS.has(token.field)
          ? t('chipExactNote')
          : ''
    return { token, label, ignored, note, tone: token.invalid ? 'invalid' : ignored ? 'ignored' : token.negated ? 'exclude' : 'normal' }
  }),
)

const TONES: Record<string, string> = {
  normal: 'border-accent-200 bg-accent-50 text-accent-700 dark:border-accent-800 dark:bg-accent-950/40 dark:text-accent-300',
  exclude: 'border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300',
  ignored: 'border-dashed border-amber-300 bg-amber-50 text-amber-800 opacity-80 dark:border-amber-700 dark:bg-amber-950/30 dark:text-amber-200',
  invalid: 'border-red-400 bg-red-100 text-red-800 dark:border-red-700 dark:bg-red-950/60 dark:text-red-200',
}
</script>

<template>
  <ul v-if="chips.length" class="flex flex-wrap gap-1.5" :aria-label="t('searchConditions')" data-testid="search-chips">
    <li v-for="chip in chips" :key="chip.token.start" class="max-w-full">
      <TooltipProvider>
        <Tooltip :disabled="!chip.note">
          <TooltipTrigger as-child>
            <span
              class="inline-flex max-w-full items-center gap-1 rounded-full border py-0.5 pl-2.5 pr-1 text-xs font-medium"
              :class="TONES[chip.tone]"
              data-testid="search-chip"
              :data-ignored="chip.ignored ? 'true' : undefined"
              :tabindex="chip.note ? 0 : undefined"
            >
              <span class="truncate">{{ chip.label }}</span>
              <span v-if="chip.ignored" class="shrink-0 text-[10px] opacity-80">· {{ chip.note }}</span>
              <button
                type="button"
                class="inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full hover:bg-black/10 dark:hover:bg-white/15"
                :aria-label="t('chipRemove', { label: chip.label })"
                data-testid="search-chip-remove"
                @click="emit('remove', chip.token)"
              >
                <X class="h-3 w-3" />
              </button>
            </span>
          </TooltipTrigger>
          <TooltipContent>{{ chip.note }}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </li>
  </ul>
</template>
