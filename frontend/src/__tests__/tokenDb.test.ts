import 'fake-indexeddb/auto'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { clearToken, getToken, setToken } from '@/lib/token-db'

async function rawWrite(value: unknown): Promise<void> {
  const request = indexedDB.open('deepresearch_flow', 1)
  await new Promise<void>((resolve, reject) => {
    request.onupgradeneeded = () => {
      request.result.createObjectStore('settings')
    }
    request.onsuccess = () => {
      const db = request.result
      const tx = db.transaction('settings', 'readwrite')
      tx.objectStore('settings').put(value, 'search_access_token')
      tx.oncomplete = () => {
        db.close()
        resolve()
      }
      tx.onerror = () => reject(tx.error)
    }
    request.onerror = () => reject(request.error)
  })
}

async function wipe(): Promise<void> {
  await new Promise<void>((resolve) => {
    const req = indexedDB.deleteDatabase('deepresearch_flow')
    req.onsuccess = req.onerror = req.onblocked = () => resolve()
  })
}

beforeEach(wipe)
afterEach(wipe)

describe('token-db', () => {
  it('returns null when unset', async () => {
    expect(await getToken()).toBeNull()
  })

  it('round-trips a token via setToken/getToken', async () => {
    await setToken('abc123')
    expect(await getToken()).toBe('abc123')
  })

  it('clears the token', async () => {
    await setToken('abc')
    await clearToken()
    expect(await getToken()).toBeNull()
  })

  it('reads legacy bare-string form', async () => {
    await rawWrite('legacy-token')
    expect(await getToken()).toBe('legacy-token')
  })

  it('reads object form {token, saved_at}', async () => {
    await rawWrite({ token: 'obj-form', saved_at: '2026-01-01T00:00:00Z' })
    expect(await getToken()).toBe('obj-form')
  })

  it('returns null for malformed object', async () => {
    await rawWrite({ not_token: 'x' })
    expect(await getToken()).toBeNull()
  })

  it('writes object form', async () => {
    await setToken('fresh')
    const request = indexedDB.open('deepresearch_flow', 1)
    const stored = await new Promise<unknown>((resolve) => {
      request.onsuccess = () => {
        const db = request.result
        const tx = db.transaction('settings', 'readonly')
        const getReq = tx.objectStore('settings').get('search_access_token')
        getReq.onsuccess = () => {
          db.close()
          resolve(getReq.result)
        }
      }
    })
    expect(stored).toMatchObject({ token: 'fresh' })
    expect((stored as { saved_at: string }).saved_at).toMatch(/^\d{4}-/)
  })
})
