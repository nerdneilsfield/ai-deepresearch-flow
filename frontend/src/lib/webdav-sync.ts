import { bytesToBase64 } from '@/lib/manual-sync-crypto'
export { createWebDavSyncSettings } from '@/lib/webdav-settings'
import { MAX_MANUAL_SYNC_ENVELOPE_BYTES } from '@/types/manual-sync'
import type {
  EncryptedManualSyncEnvelope,
  ManualSyncMetadata,
  WebDavRemoteState,
  WebDavSyncSettings,
} from '@/types/manual-sync'

const WEBDAV_REQUEST_TIMEOUT_MS = 120_000

interface TimedWebDavResponse {
  response: Response
  dispose: () => void
  didTimeout: () => boolean
}

export class WebDavConflictError extends Error {
  readonly remote: WebDavRemoteState

  constructor(remote: WebDavRemoteState) {
    super('Remote sync data has changed')
    this.name = 'WebDavConflictError'
    this.remote = remote
  }
}

export class WebDavRequestError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'WebDavRequestError'
    this.status = status
  }
}

export class WebDavResponseTooLargeError extends Error {
  constructor() {
    super('Remote sync data exceeds the 32 MiB safety limit')
    this.name = 'WebDavResponseTooLargeError'
  }
}

function requestError(response: Response): WebDavRequestError {
  if (response.status === 401 || response.status === 403) {
    return new WebDavRequestError(response.status, 'WebDAV authentication was rejected')
  }
  return new WebDavRequestError(response.status, `WebDAV request failed (${response.status})`)
}

function headersFor(settings: WebDavSyncSettings, password: string): Headers {
  if (!password) throw new Error('WebDAV password is required')
  const basic = bytesToBase64(new TextEncoder().encode(`${settings.username}:${password}`))
  return new Headers({
    Authorization: `Basic ${basic}`,
    Accept: 'application/json',
  })
}

async function request(
  settings: WebDavSyncSettings,
  password: string,
  init: RequestInit,
): Promise<TimedWebDavResponse> {
  const controller = new AbortController()
  let timedOut = false
  const timeout = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, WEBDAV_REQUEST_TIMEOUT_MS)
  try {
    const response = await fetch(settings.endpoint, {
      ...init,
      cache: 'no-store',
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal,
      headers: init.headers ?? headersFor(settings, password),
    })
    return {
      response,
      dispose: () => clearTimeout(timeout),
      didTimeout: () => timedOut,
    }
  } catch {
    clearTimeout(timeout)
    throw new Error(timedOut ? 'WebDAV request timed out' : 'Unable to reach WebDAV server')
  }
}

function strongEtag(response: Response): string | null {
  const value = response.headers.get('etag')?.trim()
  return value && /^"(?:[^"\\\r\n]|\\.)*"$/.test(value) ? value : null
}

async function readResponseTextWithinLimit(response: Response): Promise<string> {
  const contentLength = response.headers.get('content-length')
  if (contentLength) {
    const length = Number(contentLength)
    if (Number.isSafeInteger(length) && length > MAX_MANUAL_SYNC_ENVELOPE_BYTES) {
      try {
        await response.body?.cancel()
      } catch {
        // The response is still rejected because its declared size is unsafe.
      }
      throw new WebDavResponseTooLargeError()
    }
  }
  if (!response.body) return ''

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > MAX_MANUAL_SYNC_ENVELOPE_BYTES) {
        try {
          await reader.cancel()
        } catch {
          // The response is already rejected for exceeding the local safety limit.
        }
        throw new WebDavResponseTooLargeError()
      }
      chunks.push(value)
    }
  } finally {
    reader.releaseLock()
  }

  const bytes = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new Error('Remote WebDAV data is not valid UTF-8')
  }
}

export async function inspectWebDavRemote(
  settings: WebDavSyncSettings,
  password: string,
): Promise<WebDavRemoteState> {
  let timedResponse = await request(settings, password, { method: 'HEAD' })
  try {
    if (timedResponse.response.status === 405 || timedResponse.response.status === 501) {
      timedResponse.dispose()
      void timedResponse.response.body?.cancel()
      const headers = headersFor(settings, password)
      headers.set('Range', 'bytes=0-0')
      timedResponse = await request(settings, password, { method: 'GET', headers })
    }
    const { response } = timedResponse
    if (response.status === 404) {
      return { endpoint: settings.endpoint, exists: false, etag: null }
    }
    if (!response.ok) throw requestError(response)
    return {
      endpoint: settings.endpoint,
      exists: true,
      etag: strongEtag(response),
    }
  } finally {
    timedResponse.dispose()
    void timedResponse.response.body?.cancel()
  }
}

export async function downloadEncryptedWebDavSync(
  settings: WebDavSyncSettings,
  password: string,
): Promise<{ envelope: EncryptedManualSyncEnvelope; remote: WebDavRemoteState }> {
  const timedResponse = await request(settings, password, { method: 'GET' })
  try {
    const { response } = timedResponse
    if (response.status === 404) throw new WebDavRequestError(404, 'No remote sync data exists yet')
    if (!response.ok) throw requestError(response)
    const envelope = JSON.parse(await readResponseTextWithinLimit(response)) as EncryptedManualSyncEnvelope
    return {
      envelope,
      remote: {
        endpoint: settings.endpoint,
        exists: true,
        etag: strongEtag(response),
      },
    }
  } catch (error) {
    if (error instanceof WebDavResponseTooLargeError) throw error
    if (error instanceof WebDavRequestError) throw error
    if (timedResponse.didTimeout()) throw new Error('WebDAV request timed out')
    throw new Error('Remote WebDAV data is not valid JSON')
  } finally {
    timedResponse.dispose()
    void timedResponse.response.body?.cancel()
  }
}

export async function uploadEncryptedWebDavSync(
  settings: WebDavSyncSettings,
  password: string,
  envelope: EncryptedManualSyncEnvelope,
  expected: ManualSyncMetadata | null,
  force = false,
): Promise<ManualSyncMetadata> {
  const remote = await inspectWebDavRemote(settings, password)
  const headers = headersFor(settings, password)
  headers.set('Content-Type', 'application/json')

  if (!force) {
    if (!remote.exists) {
      headers.set('If-None-Match', '*')
    } else if (
      !expected ||
      expected.endpoint !== settings.endpoint ||
      !expected.etag ||
      !remote.etag ||
      expected.etag !== remote.etag
    ) {
      throw new WebDavConflictError(remote)
    } else {
      headers.set('If-Match', expected.etag)
    }
  }

  const timedResponse = await request(settings, password, {
    method: 'PUT',
    headers,
    body: JSON.stringify(envelope),
  })
  try {
    const { response } = timedResponse
    if (response.status === 412) throw new WebDavConflictError(remote)
    if (!response.ok) throw requestError(response)

    return {
      endpoint: settings.endpoint,
      etag: strongEtag(response),
      syncedAt: Date.now(),
      snapshotCreatedAt: null,
    }
  } finally {
    timedResponse.dispose()
    void timedResponse.response.body?.cancel()
  }
}
