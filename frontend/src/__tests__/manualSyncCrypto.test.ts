import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  decryptManualSyncSnapshot,
  encryptManualSyncSnapshot,
  ManualSyncCryptoError,
} from '@/lib/manual-sync-crypto'
import { createManualSyncSnapshot, ManualSyncSnapshotError } from '@/lib/manual-sync-snapshot'
import type { SearchItem } from '@/types/api'
import type { FavoriteRecord } from '@/types/favorites'
import { MAX_MANUAL_SYNC_FAVORITE_RECORDS } from '@/types/manual-sync'

function makePaper(paperId: string, title = `Paper ${paperId}`): SearchItem {
  return {
    paper_id: paperId,
    title,
    year: '2026',
    venue: 'ICLR',
    authors: ['Ada'],
  }
}

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('manual sync encryption', () => {
  it('round-trips selected papers and rated favorites without exposing their text in the envelope', async () => {
    const selected = makePaper('selected', 'Selected research title')
    const favorite: FavoriteRecord = {
      paper: makePaper('favorite', 'Private favorite title'),
      rating: 4,
      createdAt: 10,
      updatedAt: 20,
    }
    const snapshot = createManualSyncSnapshot([selected], [favorite])

    const envelope = await encryptManualSyncSnapshot(snapshot, 'long-enough-sync-passphrase')
    const restored = await decryptManualSyncSnapshot(envelope, 'long-enough-sync-passphrase')

    expect(JSON.stringify(envelope)).not.toContain('Private favorite title')
    expect(envelope.kdf).toMatchObject({ name: 'PBKDF2', hash: 'SHA-256', iterations: 600_000 })
    expect(envelope.cipher.name).toBe('AES-GCM')
    expect(restored).toEqual(snapshot)
  })

  it('rejects a different passphrase and malformed duplicate records', async () => {
    const paper = makePaper('duplicate')
    expect(() => createManualSyncSnapshot([paper, paper], [])).toThrow(ManualSyncSnapshotError)

    const snapshot = createManualSyncSnapshot([paper], [])
    const envelope = await encryptManualSyncSnapshot(snapshot, 'long-enough-sync-passphrase')

    await expect(decryptManualSyncSnapshot(envelope, 'another-long-passphrase')).rejects.toBeInstanceOf(ManualSyncCryptoError)
  })

  it('rejects an oversized favorite collection before encrypting it', () => {
    const tooManyFavorites = new Array(MAX_MANUAL_SYNC_FAVORITE_RECORDS + 1).fill({}) as FavoriteRecord[]

    expect(() => createManualSyncSnapshot([], tooManyFavorites)).toThrow(ManualSyncSnapshotError)
  })
})
