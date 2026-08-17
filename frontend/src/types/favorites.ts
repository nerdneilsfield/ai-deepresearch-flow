import type { SearchItem } from '@/types/api'

export type FavoriteRating = 1 | 2 | 3 | 4 | 5

export interface FavoriteRecord {
  paper: SearchItem
  rating: FavoriteRating
  createdAt: number
  updatedAt: number
}

export function isFavoriteRating(value: unknown): value is FavoriteRating {
  return typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 5
}
