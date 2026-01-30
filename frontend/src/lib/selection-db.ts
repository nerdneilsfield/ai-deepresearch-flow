// IndexedDB storage for selection items
// Supports hundreds of items with large metadata

const DB_NAME = 'DeepResearchDB'
const DB_VERSION = 1
const STORE_NAME = 'selection'

interface DBItem {
  paper_id: string
  data: string // JSON serialized SearchItem
  added_at: number
}

let dbPromise: Promise<IDBDatabase> | null = null

function openDB(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise
  
  dbPromise = new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)
    
    request.onerror = () => reject(request.error)
    request.onsuccess = () => resolve(request.result)
    
    request.onupgradeneeded = (event) => {
      const db = (event.target as IDBOpenDBRequest).result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        const store = db.createObjectStore(STORE_NAME, { keyPath: 'paper_id' })
        store.createIndex('added_at', 'added_at', { unique: false })
      }
    }
  })
  
  return dbPromise
}

export async function loadAllItems(): Promise<unknown[]> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly')
      const store = tx.objectStore(STORE_NAME)
      const index = store.index('added_at')
      const request = index.getAll()
      
      request.onsuccess = () => {
        const items = (request.result as DBItem[]).map(item => {
          try {
            return JSON.parse(item.data)
          } catch {
            return null
          }
        }).filter(Boolean)
        resolve(items)
      }
      request.onerror = () => reject(request.error)
    })
  } catch {
    return []
  }
}

export async function saveItem(paperId: string, data: unknown): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const item: DBItem = {
        paper_id: paperId,
        data: JSON.stringify(data),
        added_at: Date.now()
      }
      const request = store.put(item)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Ignore errors
  }
}

export async function deleteItem(paperId: string): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const request = store.delete(paperId)
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Ignore errors
  }
}

export async function clearAll(): Promise<void> {
  try {
    const db = await openDB()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      const store = tx.objectStore(STORE_NAME)
      const request = store.clear()
      request.onsuccess = () => resolve()
      request.onerror = () => reject(request.error)
    })
  } catch {
    // Ignore errors
  }
}
