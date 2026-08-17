<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, CloudDownload, CloudUpload, KeyRound, Save, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { useFavoriteStore } from '@/stores/favorites'
import { useManualSyncStore } from '@/stores/manual-sync'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
import type { ManualSyncImportMode } from '@/types/manual-sync'

const { t } = useI18n()
const selection = useSelectionStore()
const favorites = useFavoriteStore()
const sync = useManualSyncStore()
const ui = useUiStore()

const endpoint = ref('')
const username = ref('')
const password = ref('')
const passphrase = ref('')
const confirmOverwrite = ref(false)
const confirmReplace = ref(false)
const pendingApplyMode = ref<ManualSyncImportMode | null>(null)
const savingSettings = ref(false)

const settingsChanged = computed(() =>
  endpoint.value.trim() !== (sync.settings?.endpoint ?? '') ||
  username.value.trim() !== (sync.settings?.username ?? ''),
)
const canTransfer = computed(() =>
  sync.isConfigured &&
  !settingsChanged.value &&
  password.value.length > 0 &&
  passphrase.value.length >= 12 &&
  sync.busyAction === null,
)
const pending = computed(() => sync.pending)
const pendingIsOlderThanAcknowledged = computed(() => sync.pendingIsOlderThanAcknowledged)

function clearSecrets() {
  password.value = ''
  passphrase.value = ''
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : t('syncUnknownError')
}

function formatTime(value: number): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(value)
}

async function saveSettings() {
  savingSettings.value = true
  try {
    await sync.saveSettings({ endpoint: endpoint.value, username: username.value })
    endpoint.value = sync.settings?.endpoint ?? ''
    username.value = sync.settings?.username ?? ''
    ui.pushToast(t('syncConfigSaved'), 'success')
  } catch (error) {
    ui.pushToast(t('syncTransferFailed', { message: errorMessage(error) }), 'error')
  } finally {
    savingSettings.value = false
  }
}

async function upload(force = false) {
  if (!canTransfer.value) {
    ui.pushToast(t('syncSaveFirst'), 'warning')
    return
  }

  try {
    await Promise.all([selection.init(), favorites.init()])
    const outcome = await sync.upload(
      selection.items,
      favorites.items,
      password.value,
      passphrase.value,
      force,
    )
    if (outcome === 'conflict') {
      confirmOverwrite.value = false
      ui.pushToast(t('syncRemoteChanged'), 'warning')
      return
    }
    confirmOverwrite.value = false
    ui.pushToast(t('syncUploadDone'), 'success')
  } catch (error) {
    ui.pushToast(t('syncTransferFailed', { message: errorMessage(error) }), 'error')
  } finally {
    clearSecrets()
  }
}

async function download() {
  if (!canTransfer.value) {
    ui.pushToast(t('syncSaveFirst'), 'warning')
    return
  }

  try {
    await sync.download(password.value, passphrase.value)
    confirmReplace.value = false
    pendingApplyMode.value = null
    ui.pushToast(t('syncDownloadReady'), 'success')
  } catch (error) {
    ui.pushToast(t('syncTransferFailed', { message: errorMessage(error) }), 'error')
  } finally {
    clearSecrets()
  }
}

async function applyDownloaded(mode: ManualSyncImportMode) {
  const downloaded = sync.pending
  if (!downloaded) return

  try {
    const [selectedCount, favoriteCount] = await Promise.all([
      mode === 'replace'
        ? selection.replace(downloaded.snapshot.selection)
        : selection.merge(downloaded.snapshot.selection),
      mode === 'replace'
        ? favorites.replace(downloaded.snapshot.favorites)
        : favorites.merge(downloaded.snapshot.favorites),
    ])
    await sync.acceptPendingDownload()
    confirmReplace.value = false
    pendingApplyMode.value = null
    ui.pushToast(t('syncApplied', { selected: selectedCount, favorites: favoriteCount }), 'success')
  } catch (error) {
    ui.pushToast(t('syncTransferFailed', { message: errorMessage(error) }), 'error')
  }
}

function requestDownloadedApply(mode: ManualSyncImportMode) {
  if (pendingIsOlderThanAcknowledged.value) {
    pendingApplyMode.value = mode
    return
  }
  void applyDownloaded(mode)
}

function confirmOlderSnapshotApply() {
  if (!pendingApplyMode.value) return
  void applyDownloaded(pendingApplyMode.value)
}

async function forgetSettings() {
  await sync.forgetSettings()
  endpoint.value = ''
  username.value = ''
  clearSecrets()
  confirmOverwrite.value = false
  confirmReplace.value = false
  pendingApplyMode.value = null
}

onMounted(async () => {
  await Promise.all([selection.init(), favorites.init(), sync.init()])
  endpoint.value = sync.settings?.endpoint ?? ''
  username.value = sync.settings?.username ?? ''
})
</script>

<template>
  <div class="mx-auto max-w-3xl space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-foreground dark:text-ink-100">{{ t('syncTitle') }}</h1>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-400">{{ t('syncDescription') }}</p>
    </div>

    <section class="rounded-xl border border-border/60 bg-card p-5 shadow-sm dark:border-ink-700 dark:bg-ink-900/80">
      <div class="flex gap-3">
        <ShieldCheck class="mt-0.5 size-5 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
        <div class="space-y-1">
          <h2 class="font-semibold text-foreground dark:text-ink-100">{{ t('syncSecurityTitle') }}</h2>
          <p class="text-sm text-muted-foreground dark:text-ink-300">{{ t('syncSecurityDescription') }}</p>
          <p class="text-xs text-muted-foreground dark:text-ink-400">{{ t('syncLocalOnly') }}</p>
        </div>
      </div>
    </section>

    <section class="rounded-xl border border-border/60 bg-card p-5 shadow-sm dark:border-ink-700 dark:bg-ink-900/80">
      <h2 class="font-semibold text-foreground dark:text-ink-100">{{ t('syncWebDavConfig') }}</h2>
      <form class="mt-4 grid gap-4" @submit.prevent="saveSettings">
        <label class="grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="sync-endpoint">
          {{ t('syncEndpoint') }}
          <input
            id="sync-endpoint"
            v-model="endpoint"
            type="url"
            inputmode="url"
            autocomplete="off"
            required
            :placeholder="t('syncEndpointPlaceholder')"
            class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring dark:border-ink-700 dark:bg-ink-950"
          >
          <span class="text-xs font-normal text-muted-foreground dark:text-ink-400">{{ t('syncEndpointHelp') }}</span>
        </label>
        <label class="grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="sync-username">
          {{ t('syncUsername') }}
          <input
            id="sync-username"
            v-model="username"
            type="text"
            autocomplete="username"
            required
            class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring dark:border-ink-700 dark:bg-ink-950"
          >
        </label>
        <div class="flex flex-wrap items-center gap-2">
          <Button type="submit" :disabled="savingSettings">
            <Save class="mr-2 size-4" /> {{ t('syncSaveConfiguration') }}
          </Button>
          <Button v-if="sync.isConfigured" type="button" variant="outline" @click="forgetSettings">
            <Trash2 class="mr-2 size-4" /> {{ t('syncForgetConfiguration') }}
          </Button>
        </div>
      </form>
    </section>

    <section class="rounded-xl border border-border/60 bg-card p-5 shadow-sm dark:border-ink-700 dark:bg-ink-900/80">
      <div class="flex items-start gap-3">
        <KeyRound class="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <h2 class="font-semibold text-foreground dark:text-ink-100">{{ t('syncTransferTitle') }}</h2>
          <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('syncTransferDescription') }}</p>
        </div>
      </div>

      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <label class="grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="sync-password">
          {{ t('syncPassword') }}
          <input
            id="sync-password"
            v-model="password"
            type="password"
            maxlength="1024"
            autocomplete="off"
            :disabled="sync.busyAction !== null"
            class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-950"
          >
        </label>
        <label class="grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="sync-passphrase">
          {{ t('syncPassphrase') }}
          <input
            id="sync-passphrase"
            v-model="passphrase"
            type="password"
            minlength="12"
            maxlength="256"
            autocomplete="off"
            :disabled="sync.busyAction !== null"
            class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-950"
          >
          <span class="text-xs font-normal text-muted-foreground dark:text-ink-400">{{ t('syncPassphraseHelp') }}</span>
        </label>
      </div>

      <div class="mt-5 flex flex-wrap gap-2">
        <Button type="button" :disabled="!canTransfer" @click="upload()">
          <CloudUpload class="mr-2 size-4" /> {{ sync.busyAction === 'uploading' ? t('syncUploading') : t('syncUpload') }}
        </Button>
        <Button type="button" variant="outline" :disabled="!canTransfer" @click="download">
          <CloudDownload class="mr-2 size-4" /> {{ sync.busyAction === 'downloading' ? t('syncDownloading') : t('syncDownload') }}
        </Button>
      </div>
      <p class="mt-3 text-xs text-muted-foreground dark:text-ink-400">{{ t('syncCredentialsCleared') }}</p>
      <p v-if="sync.metadata" class="mt-2 text-xs text-muted-foreground dark:text-ink-400">
        {{ t('syncRemoteVersion', { time: formatTime(sync.metadata.syncedAt) }) }}
      </p>
      <p v-else class="mt-2 text-xs text-muted-foreground dark:text-ink-400">{{ t('syncNoRemoteVersion') }}</p>
    </section>

    <section
      v-if="sync.remoteConflict"
      class="rounded-xl border border-amber-300 bg-amber-50 p-5 text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
      data-testid="sync-remote-conflict"
    >
      <div class="flex gap-3">
        <AlertTriangle class="mt-0.5 size-5 shrink-0" aria-hidden="true" />
        <div class="flex-1">
          <h2 class="font-semibold">{{ t('syncRemoteChanged') }}</h2>
          <p class="mt-1 text-sm opacity-85">{{ t('syncRemoteChangedDescription') }}</p>
          <div class="mt-4 flex flex-wrap gap-2">
            <Button type="button" variant="outline" :disabled="!canTransfer" @click="download">
              <CloudDownload class="mr-2 size-4" /> {{ t('syncDownloadRemote') }}
            </Button>
            <Button v-if="!confirmOverwrite" type="button" variant="destructive" @click="confirmOverwrite = true">
              {{ t('syncPrepareOverwrite') }}
            </Button>
            <template v-else>
              <Button type="button" variant="destructive" :disabled="!canTransfer" @click="upload(true)">
                {{ t('syncConfirmOverwrite') }}
              </Button>
              <Button type="button" variant="outline" @click="confirmOverwrite = false">{{ t('syncCancel') }}</Button>
            </template>
          </div>
        </div>
      </div>
    </section>

    <section
      v-if="pending"
      class="rounded-xl border border-primary/30 bg-primary/5 p-5 dark:border-primary/40 dark:bg-primary/10"
      data-testid="sync-pending-download"
    >
      <h2 class="font-semibold text-foreground dark:text-ink-100">{{ t('syncPendingTitle') }}</h2>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('syncPendingDescription') }}</p>
      <p class="mt-2 text-sm font-medium text-foreground dark:text-ink-100">
        {{ t('syncPendingCounts', { selected: pending.snapshot.selection.length, favorites: pending.snapshot.favorites.length }) }}
      </p>
      <div class="mt-4 flex flex-wrap gap-2">
        <Button type="button" :disabled="pendingApplyMode !== null" @click="requestDownloadedApply('merge')">{{ t('syncMergeDownloaded') }}</Button>
        <Button v-if="!confirmReplace" type="button" variant="outline" :disabled="pendingApplyMode !== null" @click="confirmReplace = true">{{ t('syncPrepareReplace') }}</Button>
        <template v-else>
          <Button type="button" variant="destructive" :disabled="pendingApplyMode !== null" @click="requestDownloadedApply('replace')">{{ t('syncConfirmReplace') }}</Button>
          <Button type="button" variant="outline" :disabled="pendingApplyMode !== null" @click="confirmReplace = false">{{ t('syncCancel') }}</Button>
        </template>
        <Button type="button" variant="ghost" @click="sync.dismissPendingDownload(); pendingApplyMode = null">{{ t('syncDiscardDownloaded') }}</Button>
      </div>
      <div
        v-if="pendingIsOlderThanAcknowledged"
        class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
        data-testid="sync-older-snapshot-warning"
      >
        <p class="font-medium">{{ t('syncOlderSnapshotTitle') }}</p>
        <p class="mt-1 opacity-85">{{ t('syncOlderSnapshotDescription', { time: formatTime(pending.snapshot.createdAt) }) }}</p>
        <div v-if="pendingApplyMode" class="mt-3 flex flex-wrap gap-2">
          <Button type="button" variant="destructive" @click="confirmOlderSnapshotApply">{{ t('syncConfirmOlderSnapshot') }}</Button>
          <Button type="button" variant="outline" @click="pendingApplyMode = null">{{ t('syncCancel') }}</Button>
        </div>
      </div>
    </section>
  </div>
</template>
