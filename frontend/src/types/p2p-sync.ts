import type { EncryptedManualSyncEnvelope, ManualSyncSnapshot } from '@/types/manual-sync'

export const P2P_SYNC_SIGNAL_TYPE = 'paperdb-p2p-signal'
export const P2P_SYNC_VERSION = 1
export const P2P_DATA_CHANNEL_LABEL = 'paperdb-manual-sync-v1'
export const P2P_SIGNAL_MAX_BYTES = 512 * 1024
export const P2P_SDP_MAX_BYTES = 480 * 1024
export const P2P_MAX_ICE_SERVERS = 12
export const P2P_MAX_ICE_URLS_PER_SERVER = 8
export const P2P_ICE_URL_MAX_LENGTH = 2_048
export const P2P_ICE_USERNAME_MAX_LENGTH = 512
export const P2P_ICE_CREDENTIAL_MAX_LENGTH = 1_024
export const P2P_DATA_CHUNK_BYTES = 16 * 1024
export const P2P_MAX_BINARY_CHUNK_BYTES = 64 * 1024

export interface P2pIceServer {
  urls: string[]
  username?: string
  credential?: string
}

/** Safe to retain locally. TURN usernames and credentials are deliberately omitted. */
export interface StoredP2pIceServer {
  urls: string[]
}

export type P2pSignalDescriptionType = 'offer' | 'answer'

export interface P2pSyncSignal {
  type: typeof P2P_SYNC_SIGNAL_TYPE
  version: typeof P2P_SYNC_VERSION
  sessionId: string
  description: {
    type: P2pSignalDescriptionType
    sdp: string
  }
}

export interface P2pSyncMetadata {
  lastAcceptedSnapshotCreatedAt: number | null
}

export interface P2pPendingSnapshot {
  snapshot: ManualSyncSnapshot
  receivedAt: number
}

export interface P2pSessionEvents {
  onConnectionStateChange?: (state: RTCPeerConnectionState) => void
  onChannelStateChange?: (isOpen: boolean) => void
  onEnvelope?: (envelope: EncryptedManualSyncEnvelope) => void
  onError?: (error: Error) => void
}
