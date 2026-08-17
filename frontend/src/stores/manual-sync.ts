import { defineStore } from 'pinia'
import {
  clearManualSyncMetadata,
  clearWebDavSyncSettings,
  loadManualSyncMetadata,
  loadWebDavSyncSettings,
  saveManualSyncMetadata,
  saveWebDavSyncSettings,
} from '@/lib/local-library-db'
import { decryptManualSyncSnapshot, encryptManualSyncSnapshot } from '@/lib/manual-sync-crypto'
import { createManualSyncSnapshot } from '@/lib/manual-sync-snapshot'
import {
  createWebDavSyncSettings,
  downloadEncryptedWebDavSync,
  uploadEncryptedWebDavSync,
  WebDavConflictError,
} from '@/lib/webdav-sync'
import type { SearchItem } from '@/types/api'
import type { FavoriteRecord } from '@/types/favorites'
import type {
  DownloadedManualSync,
  ManualSyncMetadata,
  WebDavRemoteState,
  WebDavSyncSettings,
} from '@/types/manual-sync'

type BusyAction = 'uploading' | 'downloading' | null

export const useManualSyncStore = defineStore('manual-sync', {
  state: () => ({
    settings: null as WebDavSyncSettings | null,
    metadata: null as ManualSyncMetadata | null,
    pending: null as DownloadedManualSync | null,
    remoteConflict: null as WebDavRemoteState | null,
    busyAction: null as BusyAction,
    _initialized: false,
  }),
  getters: {
    isConfigured: (state) => state.settings !== null,
    pendingIsOlderThanAcknowledged: (state) =>
      state.pending !== null &&
      state.metadata?.snapshotCreatedAt !== null &&
      state.metadata?.snapshotCreatedAt !== undefined &&
      state.pending.snapshot.createdAt < state.metadata.snapshotCreatedAt,
  },
  actions: {
    async init() {
      if (this._initialized) return
      const [settings, metadata] = await Promise.all([
        loadWebDavSyncSettings(),
        loadManualSyncMetadata(),
      ])
      this.settings = settings
      this.metadata = settings && metadata?.endpoint === settings.endpoint ? metadata : null
      if (metadata && !this.metadata) await clearManualSyncMetadata()
      this._initialized = true
    },
    async saveSettings(input: Pick<WebDavSyncSettings, 'endpoint' | 'username'>) {
      await this.init()
      const next = createWebDavSyncSettings(input)
      const changed = this.settings?.endpoint !== next.endpoint || this.settings?.username !== next.username
      this.settings = next
      await saveWebDavSyncSettings(next)
      if (changed) {
        this.metadata = null
        this.pending = null
        this.remoteConflict = null
        await clearManualSyncMetadata()
      }
    },
    async forgetSettings() {
      await this.init()
      this.settings = null
      this.metadata = null
      this.pending = null
      this.remoteConflict = null
      await Promise.all([clearWebDavSyncSettings(), clearManualSyncMetadata()])
    },
    async upload(
      selection: SearchItem[],
      favorites: FavoriteRecord[],
      password: string,
      passphrase: string,
      force = false,
    ): Promise<'uploaded' | 'conflict'> {
      await this.init()
      if (!this.settings) throw new Error('Save WebDAV settings before uploading')

      this.busyAction = 'uploading'
      this.remoteConflict = null
      try {
        const snapshot = createManualSyncSnapshot(selection, favorites)
        const envelope = await encryptManualSyncSnapshot(snapshot, passphrase)
        const metadata = await uploadEncryptedWebDavSync(
          this.settings,
          password,
          envelope,
          this.metadata,
          force,
        )
        this.metadata = {
          ...metadata,
          snapshotCreatedAt: snapshot.createdAt,
        }
        await saveManualSyncMetadata(this.metadata)
        return 'uploaded'
      } catch (error) {
        if (error instanceof WebDavConflictError) {
          this.remoteConflict = error.remote
          return 'conflict'
        }
        throw error
      } finally {
        this.busyAction = null
      }
    },
    async download(password: string, passphrase: string): Promise<DownloadedManualSync> {
      await this.init()
      if (!this.settings) throw new Error('Save WebDAV settings before downloading')

      this.busyAction = 'downloading'
      try {
        const { envelope, remote } = await downloadEncryptedWebDavSync(this.settings, password)
        this.pending = {
          snapshot: await decryptManualSyncSnapshot(envelope, passphrase),
          remote,
        }
        this.remoteConflict = null
        return this.pending
      } finally {
        this.busyAction = null
      }
    },
    async acceptPendingDownload() {
      if (!this.pending) return
      this.metadata = {
        endpoint: this.pending.remote.endpoint,
        etag: this.pending.remote.etag,
        syncedAt: Date.now(),
        snapshotCreatedAt: this.pending.snapshot.createdAt,
      }
      await saveManualSyncMetadata(this.metadata)
      this.pending = null
    },
    dismissPendingDownload() {
      this.pending = null
    },
  },
})
