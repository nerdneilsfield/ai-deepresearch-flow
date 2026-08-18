import { parseEncryptedManualSyncEnvelope } from '@/lib/manual-sync-crypto'
import {
  P2P_DATA_CHANNEL_LABEL,
  P2P_DATA_CHUNK_BYTES,
  P2P_MAX_BINARY_CHUNK_BYTES,
  P2P_SDP_MAX_BYTES,
  P2P_SIGNAL_MAX_BYTES,
  P2P_SYNC_SIGNAL_TYPE,
  P2P_SYNC_VERSION,
  type P2pIceServer,
  type P2pSessionEvents,
  type P2pSignalDescriptionType,
  type P2pSyncSignal,
} from '@/types/p2p-sync'
import { MAX_MANUAL_SYNC_ENVELOPE_BYTES, type EncryptedManualSyncEnvelope } from '@/types/manual-sync'

const ICE_GATHERING_TIMEOUT_MS = 20_000
const DATA_CHANNEL_BUFFER_HIGH_WATER_MARK = 512 * 1024
const DATA_CHANNEL_BUFFER_TIMEOUT_MS = 30_000
const CONTROL_MESSAGE_MAX_BYTES = 4 * 1024
const TRANSFER_START = 'paperdb-p2p-envelope-start'
const TRANSFER_END = 'paperdb-p2p-envelope-end'

interface P2pTransferStart {
  type: typeof TRANSFER_START
  size: number
}

interface P2pTransferEnd {
  type: typeof TRANSFER_END
}

interface IncomingTransfer {
  expectedSize: number
  receivedSize: number
  chunks: Uint8Array[]
}

export class P2pSyncError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'P2pSyncError'
  }
}

function utf8ByteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength
}

function randomSessionId(): string {
  if (!globalThis.crypto?.getRandomValues) throw new P2pSyncError('Web Crypto is unavailable in this browser')
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(18))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function parseSignal(value: unknown): P2pSyncSignal {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2pSyncError('Invalid P2P connection message')
  }
  const signal = value as Partial<P2pSyncSignal>
  const description = signal.description
  if (
    signal.type !== P2P_SYNC_SIGNAL_TYPE ||
    signal.version !== P2P_SYNC_VERSION ||
    typeof signal.sessionId !== 'string' ||
    !/^[a-f0-9]{36}$/i.test(signal.sessionId) ||
    !description ||
    typeof description !== 'object' ||
    (description.type !== 'offer' && description.type !== 'answer') ||
    typeof description.sdp !== 'string' ||
    description.sdp.length === 0 ||
    utf8ByteLength(description.sdp) > P2P_SDP_MAX_BYTES
  ) {
    throw new P2pSyncError('Unsupported P2P connection message')
  }

  return {
    type: P2P_SYNC_SIGNAL_TYPE,
    version: P2P_SYNC_VERSION,
    sessionId: signal.sessionId,
    description: {
      type: description.type,
      sdp: description.sdp,
    },
  }
}

function createSignal(sessionId: string, description: RTCSessionDescriptionInit): P2pSyncSignal {
  if ((description.type !== 'offer' && description.type !== 'answer') || !description.sdp) {
    throw new P2pSyncError('Browser did not create a usable WebRTC connection message')
  }
  return parseSignal({
    type: P2P_SYNC_SIGNAL_TYPE,
    version: P2P_SYNC_VERSION,
    sessionId,
    description: {
      type: description.type,
      sdp: description.sdp,
    },
  })
}

function createLocalSignal(sessionId: string, peer: RTCPeerConnection): P2pSyncSignal {
  const description = peer.localDescription
  if (!description) throw new P2pSyncError('Browser did not create a usable WebRTC connection message')
  return createSignal(sessionId, { type: description.type, sdp: description.sdp })
}

export function serializeP2pSyncSignal(signal: P2pSyncSignal): string {
  const canonical = parseSignal(signal)
  const serialized = JSON.stringify(canonical)
  if (utf8ByteLength(serialized) > P2P_SIGNAL_MAX_BYTES) {
    throw new P2pSyncError('P2P connection message is too large')
  }
  return serialized
}

export function parseP2pSyncSignal(text: string, expectedType?: P2pSignalDescriptionType): P2pSyncSignal {
  if (typeof text !== 'string' || utf8ByteLength(text) > P2P_SIGNAL_MAX_BYTES) {
    throw new P2pSyncError('P2P connection message is too large')
  }
  try {
    const signal = parseSignal(JSON.parse(text))
    if (expectedType && signal.description.type !== expectedType) {
      throw new P2pSyncError(`Expected a P2P ${expectedType} message`)
    }
    return signal
  } catch (error) {
    if (error instanceof P2pSyncError) throw error
    throw new P2pSyncError('P2P connection message is not valid JSON')
  }
}

function toRtcIceServers(iceServers: P2pIceServer[]): RTCIceServer[] {
  return iceServers.map((server) => ({
    urls: server.urls,
    ...(server.username ? { username: server.username } : {}),
    ...(server.credential ? { credential: server.credential } : {}),
  }))
}

function createPeerConnection(iceServers: P2pIceServer[]): RTCPeerConnection {
  if (typeof globalThis.RTCPeerConnection !== 'function') {
    throw new P2pSyncError('WebRTC is unavailable in this browser')
  }
  return new globalThis.RTCPeerConnection({ iceServers: toRtcIceServers(iceServers) })
}

function waitForIceGathering(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      peer.removeEventListener('icegatheringstatechange', onStateChange)
      reject(new P2pSyncError('ICE candidate gathering timed out. Check the custom STUN/TURN configuration.'))
    }, ICE_GATHERING_TIMEOUT_MS)
    const onStateChange = () => {
      if (peer.iceGatheringState !== 'complete') return
      window.clearTimeout(timeout)
      peer.removeEventListener('icegatheringstatechange', onStateChange)
      resolve()
    }
    peer.addEventListener('icegatheringstatechange', onStateChange)
  })
}

function copyBytes(value: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(value.byteLength)
  copy.set(value)
  return copy.buffer
}

function bytesToText(chunks: Uint8Array[], length: number): string {
  const bytes = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) {
    bytes.set(chunk, offset)
    offset += chunk.byteLength
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new P2pSyncError('Received P2P sync data is not valid UTF-8')
  }
}

function parseTransferControl(value: string): P2pTransferStart | P2pTransferEnd {
  if (utf8ByteLength(value) > CONTROL_MESSAGE_MAX_BYTES) {
    throw new P2pSyncError('P2P control message is too large')
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(value)
  } catch {
    throw new P2pSyncError('Invalid P2P control message')
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new P2pSyncError('Invalid P2P control message')
  }
  const control = parsed as Partial<P2pTransferStart>
  if (control.type === TRANSFER_START) {
    const size = control.size
    if (typeof size !== 'number' || !Number.isSafeInteger(size) || size < 1 || size > MAX_MANUAL_SYNC_ENVELOPE_BYTES) {
      throw new P2pSyncError('Received P2P sync data exceeds the safety limit')
    }
    return { type: TRANSFER_START, size }
  }
  if (control.type === TRANSFER_END) return { type: TRANSFER_END }
  throw new P2pSyncError('Unsupported P2P control message')
}

async function dataToBytes(data: unknown): Promise<Uint8Array> {
  if (data instanceof ArrayBuffer) return new Uint8Array(data)
  if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength)
  if (data instanceof Blob) return new Uint8Array(await data.arrayBuffer())
  throw new P2pSyncError('Received an unsupported P2P data frame')
}

async function waitForWritableChannel(channel: RTCDataChannel): Promise<void> {
  const deadline = Date.now() + DATA_CHANNEL_BUFFER_TIMEOUT_MS
  while (channel.bufferedAmount > DATA_CHANNEL_BUFFER_HIGH_WATER_MARK) {
    if (channel.readyState !== 'open') throw new P2pSyncError('P2P connection closed during transfer')
    if (Date.now() >= deadline) throw new P2pSyncError('P2P transfer timed out while waiting for the peer')
    await new Promise<void>((resolve) => window.setTimeout(resolve, 25))
  }
}

export class P2pSyncSession {
  readonly peer: RTCPeerConnection
  readonly role: P2pSignalDescriptionType
  readonly sessionId: string

  private channel: RTCDataChannel | null = null
  private incoming: IncomingTransfer | null = null
  private closed = false
  private readonly events: P2pSessionEvents

  private constructor(
    peer: RTCPeerConnection,
    role: P2pSignalDescriptionType,
    sessionId: string,
    events: P2pSessionEvents,
  ) {
    this.peer = peer
    this.role = role
    this.sessionId = sessionId
    this.events = events
    peer.addEventListener('connectionstatechange', () => {
      this.events.onConnectionStateChange?.(peer.connectionState)
    })
  }

  get connectionState(): RTCPeerConnectionState {
    return this.peer.connectionState
  }

  get isChannelOpen(): boolean {
    return this.channel?.readyState === 'open'
  }

  private attachChannel(channel: RTCDataChannel) {
    if (this.channel) {
      channel.close()
      throw new P2pSyncError('P2P connection already has a data channel')
    }
    this.channel = channel
    channel.binaryType = 'arraybuffer'
    channel.addEventListener('open', () => this.events.onChannelStateChange?.(true))
    channel.addEventListener('close', () => this.events.onChannelStateChange?.(false))
    channel.addEventListener('error', () => {
      this.events.onError?.(new P2pSyncError('P2P data channel reported an error'))
    })
    channel.addEventListener('message', (event) => {
      void this.receive(event.data).catch((error: unknown) => {
        this.incoming = null
        this.events.onError?.(error instanceof Error ? error : new P2pSyncError('P2P receive failed'))
      })
    })
  }

  private async receive(data: unknown): Promise<void> {
    if (typeof data === 'string') {
      const control = parseTransferControl(data)
      if (control.type === TRANSFER_START) {
        if (this.incoming) throw new P2pSyncError('A P2P transfer is already in progress')
        this.incoming = { expectedSize: control.size, receivedSize: 0, chunks: [] }
        return
      }
      if (!this.incoming || this.incoming.receivedSize !== this.incoming.expectedSize) {
        throw new P2pSyncError('Received an incomplete P2P transfer')
      }
      const incoming = this.incoming
      this.incoming = null
      const envelope = parseEncryptedManualSyncEnvelope(JSON.parse(bytesToText(incoming.chunks, incoming.receivedSize)))
      this.events.onEnvelope?.(envelope)
      return
    }

    if (!this.incoming) throw new P2pSyncError('Received P2P data before its transfer header')
    const bytes = await dataToBytes(data)
    if (bytes.byteLength === 0 || bytes.byteLength > P2P_MAX_BINARY_CHUNK_BYTES) {
      throw new P2pSyncError('Received an invalid P2P data chunk')
    }
    if (this.incoming.receivedSize + bytes.byteLength > this.incoming.expectedSize) {
      throw new P2pSyncError('Received P2P data exceeds its declared size')
    }
    const copy = new Uint8Array(bytes.byteLength)
    copy.set(bytes)
    this.incoming.chunks.push(copy)
    this.incoming.receivedSize += copy.byteLength
  }

  static async createOffer(
    iceServers: P2pIceServer[],
    events: P2pSessionEvents = {},
  ): Promise<{ session: P2pSyncSession; signal: string }> {
    const peer = createPeerConnection(iceServers)
    const session = new P2pSyncSession(peer, 'offer', randomSessionId(), events)
    try {
      session.attachChannel(peer.createDataChannel(P2P_DATA_CHANNEL_LABEL, { ordered: true }))
      await peer.setLocalDescription(await peer.createOffer())
      await waitForIceGathering(peer)
      return { session, signal: serializeP2pSyncSignal(createLocalSignal(session.sessionId, peer)) }
    } catch (error) {
      session.close()
      throw error
    }
  }

  static async acceptOffer(
    offerText: string,
    iceServers: P2pIceServer[],
    events: P2pSessionEvents = {},
  ): Promise<{ session: P2pSyncSession; signal: string }> {
    const offer = parseP2pSyncSignal(offerText, 'offer')
    const peer = createPeerConnection(iceServers)
    const session = new P2pSyncSession(peer, 'answer', offer.sessionId, events)
    peer.addEventListener('datachannel', (event) => {
      try {
        session.attachChannel(event.channel)
      } catch (error) {
        events.onError?.(error instanceof Error ? error : new P2pSyncError('Unable to accept P2P data channel'))
      }
    })
    try {
      await peer.setRemoteDescription(offer.description)
      await peer.setLocalDescription(await peer.createAnswer())
      await waitForIceGathering(peer)
      return { session, signal: serializeP2pSyncSignal(createLocalSignal(session.sessionId, peer)) }
    } catch (error) {
      session.close()
      throw error
    }
  }

  async acceptAnswer(answerText: string): Promise<void> {
    if (this.role !== 'offer') throw new P2pSyncError('Only the offer device can accept an answer')
    const answer = parseP2pSyncSignal(answerText, 'answer')
    if (answer.sessionId !== this.sessionId) throw new P2pSyncError('P2P answer belongs to a different connection')
    await this.peer.setRemoteDescription(answer.description)
  }

  async sendEnvelope(envelope: EncryptedManualSyncEnvelope): Promise<void> {
    const channel = this.channel
    if (!channel || channel.readyState !== 'open') throw new P2pSyncError('P2P connection is not ready for transfer')
    const payload = new TextEncoder().encode(JSON.stringify(parseEncryptedManualSyncEnvelope(envelope)))
    if (payload.byteLength > MAX_MANUAL_SYNC_ENVELOPE_BYTES) {
      throw new P2pSyncError('P2P sync data exceeds the safety limit')
    }

    await waitForWritableChannel(channel)
    channel.send(JSON.stringify({ type: TRANSFER_START, size: payload.byteLength } satisfies P2pTransferStart))
    for (let offset = 0; offset < payload.byteLength; offset += P2P_DATA_CHUNK_BYTES) {
      await waitForWritableChannel(channel)
      channel.send(copyBytes(payload.subarray(offset, Math.min(offset + P2P_DATA_CHUNK_BYTES, payload.byteLength))))
    }
    await waitForWritableChannel(channel)
    channel.send(JSON.stringify({ type: TRANSFER_END } satisfies P2pTransferEnd))
  }

  close() {
    if (this.closed) return
    this.closed = true
    this.incoming = null
    if (this.channel && this.channel.readyState !== 'closed') this.channel.close()
    if (this.peer.connectionState !== 'closed') this.peer.close()
  }
}
