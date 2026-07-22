<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import type { AdvancedSearchParams } from '@/lib/advanced-search'
import { useAdvancedSearchAuth } from '@/composables/useAdvancedSearchAuth'
import { useUiStore } from '@/stores/ui'

const props = defineProps<{ searching?: boolean }>()
const emit = defineEmits<{
  (e: 'search', params: AdvancedSearchParams): void
}>()

const expanded = ref(false)
const tokenInput = ref('')
const queryInput = ref('')
const lastVerifyInvalid = ref(false)
const {
  authenticated,
  authMethods,
  oauthUser,
  tokenState,
  token,
  tokenFailureReason,
  hydrate,
  verifyToken,
  logoutOAuth,
  githubLoginUrl,
} = useAdvancedSearchAuth()
const ui = useUiStore()
const { t } = useI18n()
const bearerEnabled = computed(() => authMethods.value.includes('bearer'))
const githubEnabled = computed(() => authMethods.value.includes('github-oauth'))
const isVerified = computed(() => tokenState.value === 'verified')
const isVerifying = computed(() => tokenState.value === 'verifying')
const returnTo = ref(`${window.location.pathname}${window.location.search}`)
const loginHref = computed(() => githubLoginUrl(returnTo.value))

onMounted(async () => {
  const params = new URLSearchParams(window.location.search)
  const authError = params.get('auth_error')
  if (authError) {
    const key = authError === 'not_allowed'
      ? 'advancedAuthNotAllowed'
      : authError === 'denied'
        ? 'advancedAuthDenied'
        : 'advancedAuthFailed'
    ui.pushToast(t(key), 'error')
    params.delete('auth_error')
    const query = params.toString()
    returnTo.value = `${window.location.pathname}${query ? `?${query}` : ''}`
    window.history.replaceState({}, '', returnTo.value)
  }
  await hydrate()
  if (token.value) {
    tokenInput.value = token.value
  }
})

watch(
  token,
  (value, previous) => {
    if (value) {
      tokenInput.value = value
    } else if (previous) {
      tokenInput.value = ''
    }
  },
  { immediate: true },
)

watch(tokenFailureReason, (value) => {
  lastVerifyInvalid.value = value === 'invalid'
})

async function onVerify() {
  lastVerifyInvalid.value = false
  try {
    const ok = await verifyToken(tokenInput.value)
    if (!ok) {
      lastVerifyInvalid.value = true
      return
    }
    tokenInput.value = token.value ?? tokenInput.value
  } catch {
    // Keep the current input so the user can retry after transient failures.
    ui.pushToast(t('advancedTokenVerifyFailed'), 'error')
  }
}

function onSearch() {
  if (!authenticated.value || props.searching) return
  emit('search', { q: queryInput.value })
}

async function onLogout() {
  try {
    await logoutOAuth()
  } catch {
    ui.pushToast(t('advancedLogoutFailed'), 'error')
  }
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
      <span>{{ expanded ? '▼' : '▶' }} {{ t('advancedSearch') }}</span>
    </button>

    <div
      v-if="expanded"
      class="space-y-3 border-t border-border/60 p-4 dark:border-ink-700"
      data-testid="advanced-panel-body"
    >
      <div
        v-if="githubEnabled"
        class="flex flex-col gap-2 sm:flex-row sm:items-center"
        data-testid="advanced-github-auth"
      >
        <template v-if="oauthUser">
          <span class="text-sm text-green-600" data-testid="advanced-github-user">
            {{ t('advancedSignedInAs', { login: oauthUser.login }) }}
          </span>
          <button
            type="button"
            class="rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground"
            data-testid="advanced-github-logout"
            @click="onLogout"
          >
            {{ t('advancedSignOut') }}
          </button>
        </template>
        <a
          v-else
          :href="loginHref"
          class="inline-flex w-fit items-center rounded-md border border-input bg-background px-3 py-2 text-sm font-medium text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground"
          data-testid="advanced-github-login"
        >
          {{ t('advancedSignInGitHub') }}
        </a>
      </div>

      <div v-if="bearerEnabled" class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          v-model="tokenInput"
          type="password"
          :placeholder="t('advancedAccessToken')"
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
          {{ isVerifying ? t('advancedVerifying') : t('advancedVerifyToken') }}
        </button>
        <span
          v-if="isVerified"
          class="text-sm text-green-600"
          data-testid="advanced-token-status-verified"
        >✓ {{ t('advancedVerified') }}</span>
        <span
          v-else-if="lastVerifyInvalid"
          class="text-sm text-red-600"
          data-testid="advanced-token-status-invalid"
        >✗ {{ t('advancedInvalid') }}</span>
      </div>

      <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          v-model="queryInput"
          type="text"
          :placeholder="t('advancedQuery')"
          class="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:bg-muted disabled:text-muted-foreground dark:border-ink-700 dark:bg-ink-950/60 dark:text-ink-100 dark:placeholder:text-ink-500 dark:disabled:bg-ink-800/70"
          :disabled="!authenticated"
          data-testid="advanced-query-input"
        />
        <button
          type="button"
          class="rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-900 dark:text-ink-100 dark:hover:bg-ink-800"
          data-testid="advanced-search-button"
          :disabled="!authenticated || !!props.searching"
          @click="onSearch"
        >
          {{ props.searching ? t('advancedSearching') : t('advancedSearch') }}
        </button>
      </div>
    </div>
  </div>
</template>
