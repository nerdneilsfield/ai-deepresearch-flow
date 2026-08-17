import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createWebDavSyncSettings,
  downloadEncryptedWebDavSync,
  uploadEncryptedWebDavSync,
  WebDavConflictError,
  WebDavResponseTooLargeError,
} from '@/lib/webdav-sync'
import {
  MANUAL_SYNC_ENVELOPE_TYPE,
  MANUAL_SYNC_VERSION,
  MAX_MANUAL_SYNC_ENVELOPE_BYTES,
  type EncryptedManualSyncEnvelope,
} from '@/types/manual-sync'

const envelope: EncryptedManualSyncEnvelope = {
  type: MANUAL_SYNC_ENVELOPE_TYPE,
  version: MANUAL_SYNC_VERSION,
  kdf: { name: 'PBKDF2', hash: 'SHA-256', iterations: 600_000, salt: 'salt' },
  cipher: { name: 'AES-GCM', iv: 'iv' },
  ciphertext: 'ciphertext',
}

function response(status: number, body?: string, headers?: Record<string, string>): Response {
  return new Response(body, { status, headers })
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  vi.stubGlobal('fetch', fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('WebDAV manual sync transport', () => {
  it('requires a credential-free HTTPS file URL', () => {
    expect(() => createWebDavSyncSettings({ endpoint: 'http://cloud.example/papers.json', username: 'ada' })).toThrow('HTTPS')
    expect(() => createWebDavSyncSettings({ endpoint: 'https://ada:secret@cloud.example/papers.json', username: 'ada' })).toThrow('credentials')

    expect(createWebDavSyncSettings({ endpoint: ' https://cloud.example/papers.json ', username: ' ada ' })).toMatchObject({
      endpoint: 'https://cloud.example/papers.json',
      username: 'ada',
    })
  })

  it('creates a remote file with a no-overwrite precondition and records its version', async () => {
    fetchMock
      .mockResolvedValueOnce(response(404))
      .mockResolvedValueOnce(response(201, undefined, { ETag: '"v1"' }))
    const settings = createWebDavSyncSettings({ endpoint: 'https://cloud.example/papers.json', username: 'ada' })

    const metadata = await uploadEncryptedWebDavSync(settings, 'webdav-password', envelope, null)
    const put = fetchMock.mock.calls.find(([, init]) => init.method === 'PUT')

    expect(metadata).toMatchObject({ endpoint: settings.endpoint, etag: '"v1"' })
    expect(put).toBeDefined()
    expect(new Headers(put![1].headers).get('if-none-match')).toBe('*')
    expect(new Headers(put![1].headers).get('authorization')).toMatch(/^Basic /)
    expect(put![1]).toMatchObject({ redirect: 'error', referrerPolicy: 'no-referrer' })
    expect(fetchMock.mock.calls).toHaveLength(2)
  })

  it('refuses to overwrite a remote version that differs from the locally acknowledged one', async () => {
    fetchMock.mockResolvedValue(response(200, undefined, { ETag: '"remote-v2"' }))
    const settings = createWebDavSyncSettings({ endpoint: 'https://cloud.example/papers.json', username: 'ada' })

    await expect(uploadEncryptedWebDavSync(settings, 'webdav-password', envelope, {
      endpoint: settings.endpoint,
      etag: '"local-v1"',
      syncedAt: 1,
      snapshotCreatedAt: null,
    })).rejects.toBeInstanceOf(WebDavConflictError)

    expect(fetchMock.mock.calls.some(([, init]) => init.method === 'PUT')).toBe(false)
  })

  it('downloads an encrypted remote snapshot without decrypting or applying it', async () => {
    fetchMock.mockResolvedValueOnce(response(200, JSON.stringify(envelope), { ETag: '"v3"' }))
    const settings = createWebDavSyncSettings({ endpoint: 'https://cloud.example/papers.json', username: 'ada' })

    const downloaded = await downloadEncryptedWebDavSync(settings, 'webdav-password')

    expect(downloaded).toEqual({
      envelope,
      remote: { endpoint: settings.endpoint, exists: true, etag: '"v3"' },
    })
  })

  it('does not acknowledge weak or malformed remote ETags for later overwrite checks', async () => {
    fetchMock.mockResolvedValueOnce(response(200, JSON.stringify(envelope), { ETag: 'W/"v3"' }))
    const settings = createWebDavSyncSettings({ endpoint: 'https://cloud.example/papers.json', username: 'ada' })

    const downloaded = await downloadEncryptedWebDavSync(settings, 'webdav-password')

    expect(downloaded.remote.etag).toBeNull()
  })

  it('rejects an oversized remote response before parsing its contents', async () => {
    fetchMock.mockResolvedValueOnce(response(200, '', { 'Content-Length': String(MAX_MANUAL_SYNC_ENVELOPE_BYTES + 1) }))
    const settings = createWebDavSyncSettings({ endpoint: 'https://cloud.example/papers.json', username: 'ada' })

    await expect(downloadEncryptedWebDavSync(settings, 'webdav-password')).rejects.toBeInstanceOf(WebDavResponseTooLargeError)
  })
})
