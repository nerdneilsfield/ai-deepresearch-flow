const DB_NAME = 'deepresearch_flow'
const DB_VERSION = 1
const STORE_NAME = 'settings'
const KEY = 'search_access_token'

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    request.onupgradeneeded = () => {
      const db = request.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME)
      }
    }
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error ?? new Error('IDB open failed'))
  })
}

export async function getToken(): Promise<string | null> {
  try {
    const db = await openDb()
    try {
      const value = await new Promise<unknown>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readonly')
        const req = tx.objectStore(STORE_NAME).get(KEY)
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      })
      if (value && typeof value === 'object' && 'token' in value &&
          typeof (value as { token: unknown }).token === 'string') {
        return (value as { token: string }).token
      }
      if (typeof value === 'string') {
        return value
      }
      return null
    } finally {
      db.close()
    }
  } catch {
    return null
  }
}

export async function setToken(token: string): Promise<void> {
  const db = await openDb()
  try {
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put(
        { token, saved_at: new Date().toISOString() },
        KEY,
      )
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

export async function clearToken(): Promise<void> {
  try {
    const db = await openDb()
    try {
      await new Promise<void>((resolve, reject) => {
        const tx = db.transaction(STORE_NAME, 'readwrite')
        tx.objectStore(STORE_NAME).delete(KEY)
        tx.oncomplete = () => resolve()
        tx.onerror = () => reject(tx.error)
      })
    } finally {
      db.close()
    }
  } catch {
    // ignore IDB cleanup failures
  }
}
