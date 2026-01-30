<script setup lang="ts">
import { onErrorCaptured, ref } from 'vue'

const error = ref<Error | null>(null)

onErrorCaptured((err) => {
  error.value = err as Error
  console.error('ErrorBoundary caught:', err)
  return false
})

function reset() {
  error.value = null
}
</script>

<template>
  <div v-if="error" class="rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-700">
    <div class="font-semibold">Something went wrong.</div>
    <div class="mt-2 text-xs text-red-600">
      {{ error.message || 'Unknown error' }}
    </div>
    <button class="mt-4 rounded-md border border-red-200 px-3 py-1 text-xs" type="button" @click="reset">
      Retry
    </button>
  </div>
  <slot v-else />
</template>
