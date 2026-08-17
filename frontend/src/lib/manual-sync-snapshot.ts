import { MAX_BATCH_SIZE } from '@/lib/config'
import { SearchItemSchema, type SearchItem } from '@/types/api'
import { isFavoriteRating, type FavoriteRecord } from '@/types/favorites'
import {
  isManualSyncTimestamp,
  MANUAL_SYNC_SNAPSHOT_TYPE,
  MANUAL_SYNC_VERSION,
  MAX_MANUAL_SYNC_FAVORITE_RECORDS,
  type ManualSyncSnapshot,
} from '@/types/manual-sync'

export class ManualSyncSnapshotError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ManualSyncSnapshotError'
  }
}

function parseSelection(value: unknown): SearchItem[] {
  if (!Array.isArray(value) || value.length > MAX_BATCH_SIZE) {
    throw new ManualSyncSnapshotError('Invalid selected-paper list')
  }

  const ids = new Set<string>()
  return value.map((item) => {
    const parsed = SearchItemSchema.safeParse(item)
    if (!parsed.success || ids.has(parsed.data.paper_id)) {
      throw new ManualSyncSnapshotError('Invalid selected-paper record')
    }
    ids.add(parsed.data.paper_id)
    return parsed.data
  })
}

function parseFavorites(value: unknown): FavoriteRecord[] {
  if (!Array.isArray(value) || value.length > MAX_MANUAL_SYNC_FAVORITE_RECORDS) {
    throw new ManualSyncSnapshotError('Invalid favorite-paper list')
  }

  const ids = new Set<string>()
  return value.map((item) => {
    if (!item || typeof item !== 'object') throw new ManualSyncSnapshotError('Invalid favorite-paper record')
    const record = item as Partial<FavoriteRecord>
    const paper = SearchItemSchema.safeParse(record.paper)
    const createdAt = record.createdAt
    const updatedAt = record.updatedAt
    if (
      !paper.success ||
      !isFavoriteRating(record.rating) ||
      !isManualSyncTimestamp(createdAt) ||
      !isManualSyncTimestamp(updatedAt) ||
      ids.has(paper.data.paper_id)
    ) {
      throw new ManualSyncSnapshotError('Invalid favorite-paper record')
    }
    ids.add(paper.data.paper_id)
    return {
      paper: paper.data,
      rating: record.rating,
      createdAt,
      updatedAt,
    }
  })
}

export function createManualSyncSnapshot(
  selection: SearchItem[],
  favorites: FavoriteRecord[],
): ManualSyncSnapshot {
  return {
    type: MANUAL_SYNC_SNAPSHOT_TYPE,
    version: MANUAL_SYNC_VERSION,
    createdAt: Date.now(),
    selection: parseSelection(selection),
    favorites: parseFavorites(favorites),
  }
}

export function parseManualSyncSnapshot(value: unknown): ManualSyncSnapshot {
  if (!value || typeof value !== 'object') throw new ManualSyncSnapshotError('Invalid sync snapshot')
  const snapshot = value as Partial<ManualSyncSnapshot>
  const createdAt = snapshot.createdAt
  if (
    snapshot.type !== MANUAL_SYNC_SNAPSHOT_TYPE ||
    snapshot.version !== MANUAL_SYNC_VERSION ||
    !isManualSyncTimestamp(createdAt)
  ) {
    throw new ManualSyncSnapshotError('Unsupported sync snapshot')
  }

  return {
    type: MANUAL_SYNC_SNAPSHOT_TYPE,
    version: MANUAL_SYNC_VERSION,
    createdAt,
    selection: parseSelection(snapshot.selection),
    favorites: parseFavorites(snapshot.favorites),
  }
}
