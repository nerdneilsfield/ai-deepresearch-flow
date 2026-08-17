import { describe, expect, it, vi } from 'vitest'
import {
  LocalLibraryImportTooLargeError,
  MAX_LOCAL_LIBRARY_IMPORT_BYTES,
  readLocalLibraryImportText,
} from '@/lib/local-library-import'

describe('local library file import', () => {
  it('reads a normal local list file', async () => {
    await expect(readLocalLibraryImportText({ size: 16, text: async () => '{"items":[]}'})).resolves.toBe('{"items":[]}')
  })

  it('rejects an oversized file before loading its contents', async () => {
    const text = vi.fn(async () => '[]')

    await expect(readLocalLibraryImportText({ size: MAX_LOCAL_LIBRARY_IMPORT_BYTES + 1, text })).rejects.toBeInstanceOf(LocalLibraryImportTooLargeError)
    expect(text).not.toHaveBeenCalled()
  })
})
