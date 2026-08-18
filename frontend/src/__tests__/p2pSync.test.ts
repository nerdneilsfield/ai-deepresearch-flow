import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { parseP2pIceServers, stripP2pIceSecrets } from '@/lib/p2p-ice'
import { P2pSyncSession, parseP2pSyncSignal, serializeP2pSyncSignal } from '@/lib/p2p-sync'
import type { EncryptedManualSyncEnvelope } from '@/types/manual-sync'

class FakeEventTarget {
  private readonly listeners = new Map<string, Set<EventListenerOrEventListenerObject>>()

  addEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
    if (!listener) return
    const listeners = this.listeners.get(type) ?? new Set<EventListenerOrEventListenerObject>()
    listeners.add(listener)
    this.listeners.set(type, listeners)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject | null) {
    if (!listener) return
    this.listeners.get(type)?.delete(listener)
  }

  protected emit(type: string, extra: Record<string, unknown> = {}) {
    const event = Object.assign(new Event(type), extra)
    for (const listener of this.listeners.get(type) ?? []) {
      if (typeof listener === 'function') listener(event)
      else listener.handleEvent(event)
    }
  }
}

class FakeDataChannel extends FakeEventTarget {
  readyState: RTCDataChannelState = 'connecting'
  binaryType: BinaryType = 'blob'
  bufferedAmount = 0
  remote: FakeDataChannel | null = null

  send(data: string | ArrayBuffer) {
    if (this.readyState !== 'open' || !this.remote) throw new Error('Channel is not open')
    const copy = data instanceof ArrayBuffer ? data.slice(0) : data
    queueMicrotask(() => this.remote?.emit('message', { data: copy }))
  }

  open() {
    this.readyState = 'open'
    this.emit('open')
  }

  close() {
    if (this.readyState === 'closed') return
    this.readyState = 'closed'
    this.emit('close')
  }
}

class FakePeerConnection extends FakeEventTarget {
  static peers = new Map<string, FakePeerConnection>()
  static nextId = 1

  readonly id = `peer-${FakePeerConnection.nextId++}`
  readonly configuration: RTCConfiguration
  iceGatheringState: RTCIceGatheringState = 'new'
  connectionState: RTCPeerConnectionState = 'new'
  localDescription: RTCSessionDescription | null = null
  remoteDescription: RTCSessionDescription | null = null
  outgoing: FakeDataChannel | null = null

  constructor(configuration: RTCConfiguration) {
    super()
    this.configuration = configuration
    FakePeerConnection.peers.set(this.id, this)
  }

  static reset() {
    FakePeerConnection.peers.clear()
    FakePeerConnection.nextId = 1
  }

  createDataChannel() {
    this.outgoing = new FakeDataChannel()
    return this.outgoing
  }

  async createOffer(): Promise<RTCSessionDescriptionInit> {
    return { type: 'offer', sdp: `offer:${this.id}` }
  }

  async createAnswer(): Promise<RTCSessionDescriptionInit> {
    return { type: 'answer', sdp: `answer:${this.id}` }
  }

  async setLocalDescription(description: RTCSessionDescriptionInit) {
    this.localDescription = description as RTCSessionDescription
    this.iceGatheringState = 'complete'
    this.emit('icegatheringstatechange')
  }

  async setRemoteDescription(description: RTCSessionDescriptionInit) {
    this.remoteDescription = description as RTCSessionDescription
    const remoteId = description.sdp?.split(':')[1]
    if (!remoteId) throw new Error('Missing remote peer')
    const remote = FakePeerConnection.peers.get(remoteId)
    if (!remote) throw new Error('Unknown remote peer')
    if (description.type === 'answer') FakePeerConnection.connect(this, remote)
  }

  close() {
    if (this.connectionState === 'closed') return
    this.connectionState = 'closed'
    this.emit('connectionstatechange')
  }

  private static connect(offer: FakePeerConnection, answer: FakePeerConnection) {
    if (!offer.outgoing) throw new Error('Offer has no data channel')
    const answerChannel = new FakeDataChannel()
    offer.outgoing.remote = answerChannel
    answerChannel.remote = offer.outgoing
    answer.emit('datachannel', { channel: answerChannel })
    offer.connectionState = 'connected'
    answer.connectionState = 'connected'
    offer.emit('connectionstatechange')
    answer.emit('connectionstatechange')
    offer.outgoing.open()
    answerChannel.open()
  }
}

const envelope: EncryptedManualSyncEnvelope = {
  type: 'paperdb-encrypted-sync',
  version: 1,
  kdf: {
    name: 'PBKDF2',
    hash: 'SHA-256',
    iterations: 600_000,
    salt: 'AAAAAAAAAAAAAAAAAAAAAA==',
  },
  cipher: {
    name: 'AES-GCM',
    iv: 'AAAAAAAAAAAAAAAA',
  },
  ciphertext: 'AA==',
}

beforeEach(() => {
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('RTCPeerConnection', FakePeerConnection)
  FakePeerConnection.reset()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('P2P manual sync', () => {
  it('accepts custom STUN and TURN entries but removes TURN secrets before persistence', () => {
    const servers = parseP2pIceServers(JSON.stringify([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'], username: 'ada', credential: 'turn-secret' },
    ]))

    expect(servers).toEqual([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'], username: 'ada', credential: 'turn-secret' },
    ])
    expect(stripP2pIceSecrets(servers)).toEqual([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turns:turn.example.test:5349?transport=tcp'] },
    ])
  })

  it('rejects non-ICE URLs and TURN credentials attached to a STUN entry', () => {
    expect(() => parseP2pIceServers(JSON.stringify([{ urls: ['https://relay.example.test'] }]))).toThrow()
    expect(() => parseP2pIceServers(JSON.stringify([
      { urls: ['stun:stun.example.test:3478'], username: 'ada', credential: 'secret' },
    ]))).toThrow()
  })

  it('round-trips a bounded manual signal', () => {
    const text = serializeP2pSyncSignal({
      type: 'paperdb-p2p-signal',
      version: 1,
      sessionId: '0123456789abcdef0123456789abcdef0123',
      description: { type: 'offer', sdp: 'v=0\r\n' },
    })

    expect(parseP2pSyncSignal(text, 'offer')).toEqual({
      type: 'paperdb-p2p-signal',
      version: 1,
      sessionId: '0123456789abcdef0123456789abcdef0123',
      description: { type: 'offer', sdp: 'v=0\r\n' },
    })
  })

  it('transfers an encrypted envelope only after the offer and answer are explicitly exchanged', async () => {
    const received: EncryptedManualSyncEnvelope[] = []
    const offer = await P2pSyncSession.createOffer([{ urls: ['stun:stun.example.test:3478'] }])
    const answer = await P2pSyncSession.acceptOffer(offer.signal, [{ urls: ['stun:stun.example.test:3478'] }], {
      onEnvelope: (value) => received.push(value),
    })

    expect(offer.session.isChannelOpen).toBe(false)
    await offer.session.acceptAnswer(answer.signal)
    expect(offer.session.isChannelOpen).toBe(true)

    await offer.session.sendEnvelope(envelope)
    await vi.waitFor(() => expect(received).toEqual([envelope]))

    offer.session.close()
    answer.session.close()
  })
})
