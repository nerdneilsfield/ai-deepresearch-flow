<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { KeyRound } from 'lucide-vue-next'

import { useAdvancedSearchAuth } from '@/composables/useAdvancedSearchAuth'
import { useUiStore } from '@/stores/ui'

const expanded = ref(false)
const tokenInput = ref('')
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
  <div class="relative" data-testid="advanced-panel">
    <button
      type="button"
      class="inline-flex h-9 items-center gap-1.5 rounded-md border border-input bg-background px-3 text-sm font-medium text-foreground shadow-sm hover:bg-accent hover:text-accent-foreground dark:border-ink-700 dark:bg-ink-900 dark:text-ink-100 dark:hover:bg-ink-800"
      :aria-label="t('semanticAccess')"
      :aria-expanded="expanded"
      :title="t('semanticAccess')"
      data-testid="advanced-panel-toggle"
      @click="toggle"
    >
      <KeyRound class="h-4 w-4" />
      <span class="max-sm:sr-only">{{ t('semanticAccess') }}</span>
      <span
        v-if="authenticated"
        class="h-2 w-2 rounded-full bg-green-500"
        data-testid="advanced-panel-authenticated"
      ></span>
    </button>

    <div
      v-if="expanded"
      class="absolute right-0 top-full z-30 mt-2 w-[min(24rem,calc(100vw-3rem))] space-y-3 rounded-xl border border-border/60 bg-card p-4 text-card-foreground shadow-elevated-lg dark:border-ink-700 dark:bg-ink-900"
      data-testid="advanced-panel-body"
    >
      <p class="text-xs leading-relaxed text-muted-foreground" data-testid="advanced-access-help">
        {{ t('semanticAccessHelp') }}
        <template v-if="bearerEnabled"> {{ t('semanticAccessTokenHelp') }}</template>
        <template v-if="githubEnabled"> {{ t('semanticAccessGithubHelp') }}</template>
        <template v-if="!bearerEnabled && !githubEnabled"> {{ t('semanticAccessNone') }}</template>
      </p>
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

      <label v-if="bearerEnabled" for="advanced-token-input" class="block text-xs font-medium text-foreground">
        {{ t('advancedAccessToken') }}
      </label>
      <div v-if="bearerEnabled" class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <input
          id="advanced-token-input"
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
    </div>
  </div>
</template>
