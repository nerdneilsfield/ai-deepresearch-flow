<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import type { AdvancedSearchParams } from '@/lib/advanced-search'
import { useAdvancedSearchToken } from '@/composables/useAdvancedSearchToken'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{ searching?: boolean }>()
const emit = defineEmits<{
  (e: 'search', params: AdvancedSearchParams): void
}>()

const expanded = ref(false)
const tokenInput = ref('')
const queryInput = ref('')
const lastVerifyInvalid = ref(false)
const { state, token, failureReason, verify, hydrate } = useAdvancedSearchToken()
const ui = useUiStore()
const isVerified = computed(() => state.value === 'verified')
const isVerifying = computed(() => state.value === 'verifying')

onMounted(async () => {
  await hydrate()
  if (token.value) {
    tokenInput.value = token.value
  }
})

watch(token, (value, previous) => {
  if (value) {
    tokenInput.value = value
  } else if (previous) {
    tokenInput.value = ''
  }
})

watch(failureReason, (value) => {
  lastVerifyInvalid.value = value === 'invalid'
})

async function onVerify() {
  lastVerifyInvalid.value = false
  try {
    const ok = await verify(tokenInput.value)
    if (!ok) {
      lastVerifyInvalid.value = true
      return
    }
    tokenInput.value = token.value ?? tokenInput.value
  } catch {
    // Keep the current input so the user can retry after transient failures.
    ui.pushToast('Token verification failed. Please try again.', 'error')
  }
}

function onSearch() {
  if (state.value !== 'verified' || props.searching) return
  emit('search', { q: queryInput.value })
}

function toggle() {
  expanded.value = !expanded.value
}
</script>

<template>
  <div
    class="advanced-panel rounded-xl border border-border/60 bg-card text-card-foreground shadow-card dark:border-ink-700 dark:bg-ink-900/80"
    data-testid="advanced-panel"
  >
    <button
      type="button"
      class="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-foreground hover:bg-muted/60 dark:text-ink-100 dark:hover:bg-ink-800/70"
      data-testid="advanced-panel-toggle"
      @click="toggle"
    >
      <span>{{ expanded ? '▼' : '▶' }} Advanced search</span>
    </button>

    <div
      v-if="expanded"
      class="space-y-3 border-t border-border/60 p-4 dark:border-ink-700"
      data-testid="advanced-panel-body"
    >
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          v-model="tokenInput"
          type="password"
          placeholder="Access token"
          class="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:bg-muted disabled:text-muted-foreground dark:border-ink-700 dark:bg-ink-950/60 dark:text-ink-100 dark:placeholder:text-ink-500 dark:disabled:bg-ink-800/70"
          data-testid="advanced-token-input"
        />
        <button
          type="button"
          class="rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-100 dark:hover:bg-ink-800"
          data-testid="advanced-verify-button"
          :disabled="isVerifying || !tokenInput.trim()"
          @click="onVerify"
        >
          {{ isVerifying ? 'Verifying…' : 'Verify token' }}
        </button>
        <span
          v-if="isVerified"
          class="text-sm text-green-600"
          data-testid="advanced-token-status-verified"
        >✓ verified</span>
        <span
          v-else-if="lastVerifyInvalid"
          class="text-sm text-red-600"
          data-testid="advanced-token-status-invalid"
        >✗ invalid</span>
      </div>

      <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          v-model="queryInput"
          type="text"
          placeholder="Advanced query"
          class="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:bg-muted disabled:text-muted-foreground dark:border-ink-700 dark:bg-ink-950/60 dark:text-ink-100 dark:placeholder:text-ink-500 dark:disabled:bg-ink-800/70"
          :disabled="!isVerified"
          data-testid="advanced-query-input"
        />
        <button
          type="button"
          class="rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-100 dark:hover:bg-ink-800"
          data-testid="advanced-search-button"
          :disabled="!isVerified || !!props.searching"
          @click="onSearch"
        >
          {{ props.searching ? 'Searching…' : 'Advanced search' }}
        </button>
      </div>
    </div>
  </div>
</template>
