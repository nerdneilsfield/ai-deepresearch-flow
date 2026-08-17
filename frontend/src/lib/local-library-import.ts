export const MAX_LOCAL_LIBRARY_IMPORT_BYTES = 32 * 1024 * 1024

export class LocalLibraryImportTooLargeError extends Error {
  constructor() {
    super('Imported file exceeds the 32 MiB safety limit')
    this.name = 'LocalLibraryImportTooLargeError'
  }
}

export async function readLocalLibraryImportText(file: Pick<File, 'size' | 'text'>): Promise<string> {
  if (!Number.isFinite(file.size) || file.size < 0 || file.size > MAX_LOCAL_LIBRARY_IMPORT_BYTES) {
    throw new LocalLibraryImportTooLargeError()
  }
  const text = await file.text()
  if (new TextEncoder().encode(text).byteLength > MAX_LOCAL_LIBRARY_IMPORT_BYTES) {
    throw new LocalLibraryImportTooLargeError()
  }
  return text
}
