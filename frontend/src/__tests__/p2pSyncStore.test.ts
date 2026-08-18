import 'fake-indexeddb/auto'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useP2pSyncStore } from '@/stores/p2p-sync'

const DB_NAME = 'DeepResearchDB'

async function wipeDb(): Promise<void> {
  await new Promise<void>((resolve) => {
    const request = indexedDB.deleteDatabase(DB_NAME)
    request.onsuccess = request.onerror = request.onblocked = () => resolve()
  })
}

beforeEach(async () => {
  await wipeDb()
  setActivePinia(createPinia())
})

afterEach(async () => {
  await wipeDb()
})

describe('P2P sync local state', () => {
  it('retains custom ICE endpoints but never persists TURN credentials', async () => {
    const sync = useP2pSyncStore()
    await sync.saveIceServers(JSON.stringify([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'], username: 'ada', credential: 'private-turn-secret' },
    ]))

    expect(sync.iceServers).toEqual([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'] },
    ])

    setActivePinia(createPinia())
    const reloaded = useP2pSyncStore()
    await reloaded.init()
    expect(reloaded.iceServers).toEqual([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'] },
    ])
    expect(JSON.stringify(reloaded.iceServers)).not.toContain('private-turn-secret')
  })

  it('requires a second confirmation path for an older accepted P2P snapshot', () => {
    const sync = useP2pSyncStore()
    sync.metadata = { lastAcceptedSnapshotCreatedAt: 20 }
    sync.pending = {
      snapshot: {
        type: 'paperdb-manual-sync',
        version: 1,
        createdAt: 10,
        selection: [],
        favorites: [],
      },
      receivedAt: 30,
    }

    expect(sync.pendingIsOlderThanAccepted).toBe(true)
  })
})
