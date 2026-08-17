import { isManualSyncTimestamp, type WebDavSyncSettings } from '@/types/manual-sync'

export function createWebDavSyncSettings(input: Pick<WebDavSyncSettings, 'endpoint' | 'username'>): WebDavSyncSettings {
  const endpoint = input.endpoint.trim()
  const username = input.username.trim()
  if (!username) throw new Error('WebDAV username is required')

  let url: URL
  try {
    url = new URL(endpoint)
  } catch {
    throw new Error('WebDAV file URL is invalid')
  }
  if (url.protocol !== 'https:') throw new Error('WebDAV file URL must use HTTPS')
  if (url.username || url.password || url.search || url.hash) {
    throw new Error('WebDAV file URL cannot contain credentials, queries, or fragments')
  }

  return {
    provider: 'webdav',
    endpoint: url.toString(),
    username,
    updatedAt: Date.now(),
  }
}

export function parseStoredWebDavSyncSettings(value: unknown): WebDavSyncSettings | null {
  if (!value || typeof value !== 'object') return null
  const settings = value as Partial<WebDavSyncSettings>
  if (settings.provider !== 'webdav' || typeof settings.endpoint !== 'string' || typeof settings.username !== 'string') {
    return null
  }
  if (!isManualSyncTimestamp(settings.updatedAt)) return null

  try {
    return {
      ...createWebDavSyncSettings({ endpoint: settings.endpoint, username: settings.username }),
      updatedAt: settings.updatedAt,
    }
  } catch {
    return null
  }
}
