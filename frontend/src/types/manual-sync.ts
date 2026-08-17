import type { SearchItem } from '@/types/api'
import type { FavoriteRecord } from '@/types/favorites'

export const MANUAL_SYNC_SNAPSHOT_TYPE = 'paperdb-manual-sync'
export const MANUAL_SYNC_ENVELOPE_TYPE = 'paperdb-encrypted-sync'
export const MANUAL_SYNC_VERSION = 1
export const MAX_MANUAL_SYNC_PLAINTEXT_BYTES = 20 * 1024 * 1024
export const MAX_MANUAL_SYNC_ENVELOPE_BYTES = 32 * 1024 * 1024
export const MAX_MANUAL_SYNC_FAVORITE_RECORDS = 100_000

export function isManualSyncTimestamp(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value) && Math.abs(value) <= 8_640_000_000_000_000
}

export interface ManualSyncSnapshot {
  type: typeof MANUAL_SYNC_SNAPSHOT_TYPE
  version: typeof MANUAL_SYNC_VERSION
  createdAt: number
  selection: SearchItem[]
  favorites: FavoriteRecord[]
}

export interface EncryptedManualSyncEnvelope {
  type: typeof MANUAL_SYNC_ENVELOPE_TYPE
  version: typeof MANUAL_SYNC_VERSION
  kdf: {
    name: 'PBKDF2'
    hash: 'SHA-256'
    iterations: number
    salt: string
  }
  cipher: {
    name: 'AES-GCM'
    iv: string
  }
  ciphertext: string
}

export interface WebDavSyncSettings {
  provider: 'webdav'
  endpoint: string
  username: string
  updatedAt: number
}

export interface ManualSyncMetadata {
  endpoint: string
  etag: string | null
  syncedAt: number
  snapshotCreatedAt: number | null
}

export interface WebDavRemoteState {
  endpoint: string
  exists: boolean
  etag: string | null
}

export interface DownloadedManualSync {
  snapshot: ManualSyncSnapshot
  remote: WebDavRemoteState
}

export type ManualSyncImportMode = 'merge' | 'replace'
