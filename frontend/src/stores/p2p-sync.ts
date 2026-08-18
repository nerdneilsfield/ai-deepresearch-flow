import { markRaw } from 'vue'
import { defineStore } from 'pinia'
import {
  loadP2pIceServers,
  loadP2pSyncMetadata,
  saveP2pIceServers,
  saveP2pSyncMetadata,
} from '@/lib/local-library-db'
import { decryptManualSyncSnapshot, encryptManualSyncSnapshot } from '@/lib/manual-sync-crypto'
import { createManualSyncSnapshot } from '@/lib/manual-sync-snapshot'
import { parseP2pIceServers, stripP2pIceSecrets } from '@/lib/p2p-ice'
import { P2pSyncError, P2pSyncSession } from '@/lib/p2p-sync'
import type { SearchItem } from '@/types/api'
import type { FavoriteRecord } from '@/types/favorites'
import type { EncryptedManualSyncEnvelope } from '@/types/manual-sync'
import type {
  P2pPendingSnapshot,
  P2pSessionEvents,
  P2pSyncMetadata,
  StoredP2pIceServer,
} from '@/types/p2p-sync'

type P2pBusyAction = 'creating-offer' | 'accepting-offer' | 'accepting-answer' | 'sending' | 'decrypting' | null

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'P2P transfer failed'
}

export const useP2pSyncStore = defineStore('p2p-sync', {
  state: () => ({
    iceServers: [] as StoredP2pIceServer[],
    metadata: null as P2pSyncMetadata | null,
    session: null as P2pSyncSession | null,
    role: null as 'offer' | 'answer' | null,
    localSignal: null as string | null,
    connectionState: 'idle' as RTCPeerConnectionState | 'idle',
    channelOpen: false,
    receivedEnvelope: null as EncryptedManualSyncEnvelope | null,
    pending: null as P2pPendingSnapshot | null,
    busyAction: null as P2pBusyAction,
    lastError: null as string | null,
    _initialized: false,
    _sessionGeneration: 0,
  }),
  getters: {
    isConnected: (state) => state.channelOpen && state.connectionState === 'connected',
    pendingIsOlderThanAccepted: (state) =>
      state.pending !== null &&
      state.metadata?.lastAcceptedSnapshotCreatedAt !== null &&
      state.metadata?.lastAcceptedSnapshotCreatedAt !== undefined &&
      state.pending.snapshot.createdAt < state.metadata.lastAcceptedSnapshotCreatedAt,
  },
  actions: {
    async init() {
      if (this._initialized) return
      const [iceServers, metadata] = await Promise.all([loadP2pIceServers(), loadP2pSyncMetadata()])
      this.iceServers = iceServers
      this.metadata = metadata
      this._initialized = true
    },
    async saveIceServers(text: string) {
      await this.init()
      const servers = stripP2pIceSecrets(parseP2pIceServers(text))
      this.iceServers = servers
      await saveP2pIceServers(servers)
      return servers
    },
    sessionEvents(generation: number): P2pSessionEvents {
      return {
        onConnectionStateChange: (state) => {
          if (this._sessionGeneration !== generation) return
          this.connectionState = state
        },
        onChannelStateChange: (isOpen) => {
          if (this._sessionGeneration !== generation) return
          this.channelOpen = isOpen
        },
        onEnvelope: (envelope) => {
          if (this._sessionGeneration !== generation) return
          if (this.receivedEnvelope || this.pending) {
            this.lastError = 'Apply or discard the earlier received transfer before accepting another one'
            return
          }
          this.receivedEnvelope = envelope
          this.lastError = null
        },
        onError: (error) => {
          if (this._sessionGeneration !== generation) return
          this.lastError = errorMessage(error)
        },
      }
    },
    closeSession() {
      this._sessionGeneration += 1
      this.session?.close()
      this.session = null
      this.role = null
      this.localSignal = null
      this.connectionState = 'idle'
      this.channelOpen = false
      this.busyAction = null
    },
    async createOffer(iceServerText: string): Promise<string> {
      await this.init()
      const iceServers = parseP2pIceServers(iceServerText)
      this.closeSession()
      const generation = this._sessionGeneration
      this.busyAction = 'creating-offer'
      this.lastError = null
      try {
        const created = await P2pSyncSession.createOffer(iceServers, this.sessionEvents(generation))
        if (this._sessionGeneration !== generation) {
          created.session.close()
          throw new P2pSyncError('P2P connection setup was cancelled')
        }
        this.session = markRaw(created.session)
        this.role = 'offer'
        this.localSignal = created.signal
        this.connectionState = created.session.connectionState
        this.channelOpen = created.session.isChannelOpen
        return created.signal
      } catch (error) {
        this.lastError = errorMessage(error)
        throw error
      } finally {
        if (this._sessionGeneration === generation) this.busyAction = null
      }
    },
    async acceptOffer(offerText: string, iceServerText: string): Promise<string> {
      await this.init()
      const iceServers = parseP2pIceServers(iceServerText)
      this.closeSession()
      const generation = this._sessionGeneration
      this.busyAction = 'accepting-offer'
      this.lastError = null
      try {
        const created = await P2pSyncSession.acceptOffer(offerText, iceServers, this.sessionEvents(generation))
        if (this._sessionGeneration !== generation) {
          created.session.close()
          throw new P2pSyncError('P2P connection setup was cancelled')
        }
        this.session = markRaw(created.session)
        this.role = 'answer'
        this.localSignal = created.signal
        this.connectionState = created.session.connectionState
        this.channelOpen = created.session.isChannelOpen
        return created.signal
      } catch (error) {
        this.lastError = errorMessage(error)
        throw error
      } finally {
        if (this._sessionGeneration === generation) this.busyAction = null
      }
    },
    async acceptAnswer(answerText: string) {
      if (!this.session || this.role !== 'offer') throw new P2pSyncError('Create a P2P offer before accepting an answer')
      const generation = this._sessionGeneration
      this.busyAction = 'accepting-answer'
      this.lastError = null
      try {
        await this.session.acceptAnswer(answerText)
        if (this._sessionGeneration !== generation) throw new P2pSyncError('P2P connection setup was cancelled')
        this.localSignal = null
        this.connectionState = this.session.connectionState
        this.channelOpen = this.session.isChannelOpen
      } catch (error) {
        this.lastError = errorMessage(error)
        throw error
      } finally {
        if (this._sessionGeneration === generation) this.busyAction = null
      }
    },
    async send(selection: SearchItem[], favorites: FavoriteRecord[], passphrase: string) {
      if (!this.session || !this.isConnected) throw new P2pSyncError('P2P connection is not ready for transfer')
      this.busyAction = 'sending'
      this.lastError = null
      try {
        const snapshot = createManualSyncSnapshot(selection, favorites)
        const envelope = await encryptManualSyncSnapshot(snapshot, passphrase)
        await this.session.sendEnvelope(envelope)
      } catch (error) {
        this.lastError = errorMessage(error)
        throw error
      } finally {
        this.busyAction = null
      }
    },
    async decryptReceived(passphrase: string): Promise<P2pPendingSnapshot> {
      if (!this.receivedEnvelope) throw new P2pSyncError('No encrypted P2P transfer is waiting')
      this.busyAction = 'decrypting'
      this.lastError = null
      try {
        const pending: P2pPendingSnapshot = {
          snapshot: await decryptManualSyncSnapshot(this.receivedEnvelope, passphrase),
          receivedAt: Date.now(),
        }
        this.pending = pending
        this.receivedEnvelope = null
        return pending
      } catch (error) {
        this.lastError = errorMessage(error)
        throw error
      } finally {
        this.busyAction = null
      }
    },
    dismissReceived() {
      this.receivedEnvelope = null
    },
    async acceptPending() {
      if (!this.pending) return
      this.metadata = { lastAcceptedSnapshotCreatedAt: this.pending.snapshot.createdAt }
      await saveP2pSyncMetadata(this.metadata)
      this.pending = null
    },
    dismissPending() {
      this.pending = null
    },
  },
})
