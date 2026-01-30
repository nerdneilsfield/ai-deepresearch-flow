export const API_BASE = (import.meta.env.VITE_PAPER_DB_API_BASE || '/api/v1').replace(/\/$/, '')
export const STATIC_BASE = (import.meta.env.VITE_PAPER_DB_STATIC_BASE || '').replace(/\/$/, '')

export const DEFAULT_PAGE_SIZE = 20
export const MAX_BATCH_SIZE = 10000

export const SEARCH_DEBOUNCE_MS = 300
export const SEARCH_TIMEOUT_MS = 10_000
