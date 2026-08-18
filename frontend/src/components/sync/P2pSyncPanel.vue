<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Cable, Copy, KeyRound, Link2, Radio, Save, Send, ShieldCheck, Trash2 } from 'lucide-vue-next'
import { Button } from '@/components/ui/button'
import { formatStoredP2pIceServers } from '@/lib/p2p-ice'
import { useFavoriteStore } from '@/stores/favorites'
import { useP2pSyncStore } from '@/stores/p2p-sync'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
import type { ManualSyncImportMode } from '@/types/manual-sync'

const { t } = useI18n()
const selection = useSelectionStore()
const favorites = useFavoriteStore()
const p2p = useP2pSyncStore()
const ui = useUiStore()

const iceServersText = ref('[]')
const remoteSignal = ref('')
const passphrase = ref('')
const savingIce = ref(false)
const confirmReplace = ref(false)
const pendingApplyMode = ref<ManualSyncImportMode | null>(null)

const isBusy = computed(() => p2p.busyAction !== null)
const canSend = computed(() => p2p.isConnected && passphrase.value.length >= 12 && !isBusy.value)
const signalAction = computed(() => t(p2p.role === 'answer' ? 'p2pAnswer' : 'p2pOffer'))

function clearPassphrase() {
  passphrase.value = ''
}

function formatTime(value: number): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(value)
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : t('syncUnknownError')
}

async function saveIceServers() {
  savingIce.value = true
  try {
    await p2p.saveIceServers(iceServersText.value)
    ui.pushToast(t('p2pIceSaved'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  } finally {
    savingIce.value = false
  }
}

async function createOffer() {
  try {
    await p2p.createOffer(iceServersText.value)
    ui.pushToast(t('p2pOfferReady'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  }
}

async function acceptOffer() {
  try {
    await p2p.acceptOffer(remoteSignal.value, iceServersText.value)
    remoteSignal.value = ''
    ui.pushToast(t('p2pAnswerReady'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  }
}

async function acceptAnswer() {
  try {
    await p2p.acceptAnswer(remoteSignal.value)
    remoteSignal.value = ''
    ui.pushToast(t('p2pConnecting'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  }
}

async function copyLocalSignal() {
  if (!p2p.localSignal) return
  try {
    if (!navigator.clipboard?.writeText) throw new Error(t('p2pClipboardUnavailable'))
    await navigator.clipboard.writeText(p2p.localSignal)
    ui.pushToast(t('p2pSignalCopied'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'warning')
  }
}

async function send() {
  if (!canSend.value) {
    ui.pushToast(t('p2pConnectFirst'), 'warning')
    return
  }
  try {
    await Promise.all([selection.init(), favorites.init()])
    await p2p.send(selection.items, favorites.items, passphrase.value)
    ui.pushToast(t('p2pSent'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  } finally {
    clearPassphrase()
  }
}

async function decryptReceived() {
  if (passphrase.value.length < 12) {
    ui.pushToast(t('p2pPassphraseRequired'), 'warning')
    return
  }
  try {
    await p2p.decryptReceived(passphrase.value)
    pendingApplyMode.value = null
    ui.pushToast(t('p2pDecrypted'), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  } finally {
    clearPassphrase()
  }
}

async function applyPending(mode: ManualSyncImportMode) {
  const pending = p2p.pending
  if (!pending) return
  try {
    const [selectedCount, favoriteCount] = await Promise.all([
      mode === 'replace'
        ? selection.replace(pending.snapshot.selection)
        : selection.merge(pending.snapshot.selection),
      mode === 'replace'
        ? favorites.replace(pending.snapshot.favorites)
        : favorites.merge(pending.snapshot.favorites),
    ])
    await p2p.acceptPending()
    confirmReplace.value = false
    pendingApplyMode.value = null
    ui.pushToast(t('syncApplied', { selected: selectedCount, favorites: favoriteCount }), 'success')
  } catch (error) {
    ui.pushToast(t('p2pFailed', { message: errorMessage(error) }), 'error')
  }
}

function requestPendingApply(mode: ManualSyncImportMode) {
  if (p2p.pendingIsOlderThanAccepted) {
    pendingApplyMode.value = mode
    return
  }
  void applyPending(mode)
}

function confirmOlderSnapshotApply() {
  if (!pendingApplyMode.value) return
  void applyPending(pendingApplyMode.value)
}

function closeSession() {
  p2p.closeSession()
  remoteSignal.value = ''
  clearPassphrase()
}

onMounted(async () => {
  await Promise.all([selection.init(), favorites.init(), p2p.init()])
  iceServersText.value = formatStoredP2pIceServers(p2p.iceServers)
})
</script>

<template>
  <section
    class="rounded-xl border border-border/60 bg-card p-5 shadow-sm dark:border-ink-700 dark:bg-ink-900/80"
    data-testid="p2p-sync-panel"
  >
    <div class="flex gap-3">
      <Radio class="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
      <div>
        <h2 class="font-semibold text-foreground dark:text-ink-100">{{ t('p2pTitle') }}</h2>
        <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pDescription') }}</p>
      </div>
    </div>

    <div class="mt-5 rounded-lg border border-border/60 bg-muted/30 p-4 dark:border-ink-700 dark:bg-ink-950/40">
      <div class="flex gap-3">
        <ShieldCheck class="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
        <p class="text-xs text-muted-foreground dark:text-ink-300">{{ t('p2pSecurity') }}</p>
      </div>
    </div>

    <div class="mt-5">
      <h3 class="font-medium text-foreground dark:text-ink-100">{{ t('p2pIceTitle') }}</h3>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pIceDescription') }}</p>
      <label class="mt-3 grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="p2p-ice-servers">
        {{ t('p2pIceLabel') }}
        <textarea
          id="p2p-ice-servers"
          v-model="iceServersText"
          data-testid="p2p-ice-servers"
          rows="7"
          spellcheck="false"
          autocapitalize="off"
          autocomplete="off"
          :disabled="isBusy"
          :placeholder="t('p2pIcePlaceholder')"
          class="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm outline-none transition-colors placeholder:font-sans placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-950"
        />
        <span class="text-xs font-normal text-muted-foreground dark:text-ink-400">{{ t('p2pIceHelp') }}</span>
      </label>
      <Button class="mt-3" type="button" variant="outline" :disabled="savingIce || isBusy" @click="saveIceServers">
        <Save class="mr-2 size-4" /> {{ t('p2pSaveIce') }}
      </Button>
    </div>

    <div class="mt-6 border-t border-border/60 pt-5 dark:border-ink-700">
      <div class="flex gap-3">
        <Link2 class="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <h3 class="font-medium text-foreground dark:text-ink-100">{{ t('p2pConnectionTitle') }}</h3>
          <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pConnectionDescription') }}</p>
        </div>
      </div>

      <div class="mt-4 flex flex-wrap gap-2">
        <Button type="button" :disabled="isBusy || p2p.role !== null" @click="createOffer">
          <Cable class="mr-2 size-4" /> {{ p2p.busyAction === 'creating-offer' ? t('p2pCreatingOffer') : t('p2pCreateOffer') }}
        </Button>
        <Button type="button" variant="outline" :disabled="isBusy || p2p.role !== null || !remoteSignal.trim()" @click="acceptOffer">
          {{ p2p.busyAction === 'accepting-offer' ? t('p2pCreatingAnswer') : t('p2pAcceptOffer') }}
        </Button>
        <Button v-if="p2p.role === 'offer'" type="button" variant="outline" :disabled="isBusy || !remoteSignal.trim()" @click="acceptAnswer">
          {{ p2p.busyAction === 'accepting-answer' ? t('p2pCompletingConnection') : t('p2pAcceptAnswer') }}
        </Button>
        <Button v-if="p2p.role !== null" type="button" variant="ghost" :disabled="isBusy" @click="closeSession">
          <Trash2 class="mr-2 size-4" /> {{ t('p2pCloseConnection') }}
        </Button>
      </div>

      <label class="mt-4 grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="p2p-remote-signal">
        {{ p2p.role === 'offer' ? t('p2pAnswerInput') : t('p2pOfferInput') }}
        <textarea
          id="p2p-remote-signal"
          v-model="remoteSignal"
          data-testid="p2p-remote-signal"
          rows="5"
          spellcheck="false"
          autocapitalize="off"
          autocomplete="off"
          :disabled="isBusy || p2p.role === 'answer'"
          :placeholder="t('p2pSignalPlaceholder')"
          class="w-full rounded-md border border-input bg-background px-3 py-2 font-mono text-xs shadow-sm outline-none transition-colors placeholder:font-sans placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-950"
        />
      </label>

      <div v-if="p2p.localSignal" class="mt-4 rounded-lg border border-primary/30 bg-primary/5 p-4 dark:border-primary/40 dark:bg-primary/10">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <p class="text-sm font-medium text-foreground dark:text-ink-100">{{ t('p2pLocalSignal', { type: signalAction }) }}</p>
          <Button type="button" size="sm" variant="outline" @click="copyLocalSignal">
            <Copy class="mr-2 size-3.5" /> {{ t('p2pCopySignal') }}
          </Button>
        </div>
        <textarea
          readonly
          rows="5"
          data-testid="p2p-local-signal"
          :value="p2p.localSignal"
          class="mt-3 w-full rounded-md border border-primary/20 bg-background px-3 py-2 font-mono text-xs outline-none dark:border-primary/30 dark:bg-ink-950"
        />
      </div>

      <p class="mt-3 text-xs text-muted-foreground dark:text-ink-400">
        {{ t('p2pConnectionState', { state: p2p.connectionState }) }}
      </p>
      <p v-if="p2p.lastError" class="mt-2 text-sm text-destructive" data-testid="p2p-error">{{ p2p.lastError }}</p>
    </div>

    <div class="mt-6 border-t border-border/60 pt-5 dark:border-ink-700">
      <div class="flex gap-3">
        <KeyRound class="mt-0.5 size-5 shrink-0 text-primary" aria-hidden="true" />
        <div>
          <h3 class="font-medium text-foreground dark:text-ink-100">{{ t('p2pTransferTitle') }}</h3>
          <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pTransferDescription') }}</p>
        </div>
      </div>

      <label class="mt-4 grid gap-1.5 text-sm font-medium text-foreground dark:text-ink-200" for="p2p-passphrase">
        {{ t('syncPassphrase') }}
        <input
          id="p2p-passphrase"
          v-model="passphrase"
          type="password"
          minlength="12"
          maxlength="256"
          autocomplete="off"
          :disabled="isBusy"
          class="flex h-10 w-full rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm outline-none transition-colors focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 dark:border-ink-700 dark:bg-ink-950"
        >
        <span class="text-xs font-normal text-muted-foreground dark:text-ink-400">{{ t('p2pPassphraseHelp') }}</span>
      </label>

      <div class="mt-4 flex flex-wrap gap-2">
        <Button type="button" :disabled="!canSend" @click="send">
          <Send class="mr-2 size-4" /> {{ p2p.busyAction === 'sending' ? t('p2pSending') : t('p2pSend') }}
        </Button>
      </div>
      <p class="mt-3 text-xs text-muted-foreground dark:text-ink-400">{{ t('p2pSecretsCleared') }}</p>
    </div>

    <div
      v-if="p2p.receivedEnvelope"
      class="mt-6 rounded-lg border border-primary/30 bg-primary/5 p-4 dark:border-primary/40 dark:bg-primary/10"
      data-testid="p2p-received-envelope"
    >
      <h3 class="font-medium text-foreground dark:text-ink-100">{{ t('p2pReceivedTitle') }}</h3>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pReceivedDescription') }}</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <Button type="button" :disabled="isBusy || passphrase.length < 12" @click="decryptReceived">
          {{ p2p.busyAction === 'decrypting' ? t('p2pDecrypting') : t('p2pDecryptReceived') }}
        </Button>
        <Button type="button" variant="ghost" :disabled="isBusy" @click="p2p.dismissReceived()">{{ t('p2pDiscardReceived') }}</Button>
      </div>
    </div>

    <div
      v-if="p2p.pending"
      class="mt-6 rounded-lg border border-primary/30 bg-primary/5 p-4 dark:border-primary/40 dark:bg-primary/10"
      data-testid="p2p-pending-snapshot"
    >
      <h3 class="font-medium text-foreground dark:text-ink-100">{{ t('p2pPendingTitle') }}</h3>
      <p class="mt-1 text-sm text-muted-foreground dark:text-ink-300">{{ t('p2pPendingDescription') }}</p>
      <p class="mt-2 text-sm font-medium text-foreground dark:text-ink-100">
        {{ t('syncPendingCounts', { selected: p2p.pending.snapshot.selection.length, favorites: p2p.pending.snapshot.favorites.length }) }}
      </p>
      <div class="mt-4 flex flex-wrap gap-2">
        <Button type="button" :disabled="pendingApplyMode !== null" @click="requestPendingApply('merge')">{{ t('syncMergeDownloaded') }}</Button>
        <Button v-if="!confirmReplace" type="button" variant="outline" :disabled="pendingApplyMode !== null" @click="confirmReplace = true">{{ t('syncPrepareReplace') }}</Button>
        <template v-else>
          <Button type="button" variant="destructive" :disabled="pendingApplyMode !== null" @click="requestPendingApply('replace')">{{ t('syncConfirmReplace') }}</Button>
          <Button type="button" variant="outline" :disabled="pendingApplyMode !== null" @click="confirmReplace = false">{{ t('syncCancel') }}</Button>
        </template>
        <Button type="button" variant="ghost" @click="p2p.dismissPending(); pendingApplyMode = null">{{ t('syncDiscardDownloaded') }}</Button>
      </div>
      <div
        v-if="p2p.pendingIsOlderThanAccepted"
        class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 text-sm text-amber-950 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100"
        data-testid="p2p-older-snapshot-warning"
      >
        <p class="font-medium">{{ t('p2pOlderSnapshotTitle') }}</p>
        <p class="mt-1 opacity-85">{{ t('p2pOlderSnapshotDescription', { time: formatTime(p2p.pending.snapshot.createdAt) }) }}</p>
        <div v-if="pendingApplyMode" class="mt-3 flex flex-wrap gap-2">
          <Button type="button" variant="destructive" @click="confirmOlderSnapshotApply">{{ t('p2pConfirmOlderSnapshot') }}</Button>
          <Button type="button" variant="outline" @click="pendingApplyMode = null">{{ t('syncCancel') }}</Button>
        </div>
      </div>
    </div>
  </section>
</template>
