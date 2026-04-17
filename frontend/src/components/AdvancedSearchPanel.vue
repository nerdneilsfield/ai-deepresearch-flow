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
const { state, token, verify, hydrate } = useAdvancedSearchToken()
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
  <div class="advanced-panel rounded-xl border border-ink-100 bg-white">
    <button
      type="button"
      class="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-ink-900"
      data-testid="advanced-panel-toggle"
      @click="toggle"
    >
      <span>{{ expanded ? '▼' : '▶' }} Advanced search</span>
    </button>

    <div
      v-if="expanded"
      class="space-y-3 border-t border-ink-100 p-4"
      data-testid="advanced-panel-body"
    >
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          v-model="tokenInput"
          type="password"
          placeholder="Access token"
          class="min-w-0 flex-1 rounded-md border border-ink-200 px-3 py-2 text-sm"
          data-testid="advanced-token-input"
        />
        <button
          type="button"
          class="rounded-md border border-ink-200 px-3 py-2 text-sm"
          data-testid="advanced-verify-button"
          :disabled="isVerifying"
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
          class="min-w-0 flex-1 rounded-md border border-ink-200 px-3 py-2 text-sm"
          :disabled="!isVerified"
          data-testid="advanced-query-input"
        />
        <button
          type="button"
          class="rounded-md border border-ink-200 px-3 py-2 text-sm"
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
