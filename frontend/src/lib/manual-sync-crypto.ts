import { parseManualSyncSnapshot } from '@/lib/manual-sync-snapshot'
import {
  MANUAL_SYNC_ENVELOPE_TYPE,
  MANUAL_SYNC_VERSION,
  MAX_MANUAL_SYNC_PLAINTEXT_BYTES,
  type EncryptedManualSyncEnvelope,
  type ManualSyncSnapshot,
} from '@/types/manual-sync'

const KDF_ITERATIONS = 600_000
const SALT_LENGTH = 16
const IV_LENGTH = 12
const AES_GCM_TAG_LENGTH = 16
const ADDITIONAL_DATA = new TextEncoder().encode('paperdb-manual-sync:v1')

export class ManualSyncCryptoError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ManualSyncCryptoError'
  }
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunkSize = 0x8000
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + chunkSize))
  }
  return btoa(binary)
}

function maximumBase64Length(byteLength: number): number {
  return Math.ceil(byteLength / 3) * 4 + 4
}

export function base64ToBytes(value: string, maxBytes?: number): Uint8Array {
  try {
    if (maxBytes !== undefined && value.length > maximumBase64Length(maxBytes)) {
      throw new ManualSyncCryptoError('Encrypted sync data is too large')
    }
    const binary = atob(value)
    if (maxBytes !== undefined && binary.length > maxBytes) {
      throw new ManualSyncCryptoError('Encrypted sync data is too large')
    }
    const bytes = new Uint8Array(binary.length)
    for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index)
    return bytes
  } catch (error) {
    if (error instanceof ManualSyncCryptoError) throw error
    throw new ManualSyncCryptoError('Invalid encrypted sync data')
  }
}

function toArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  const copy = new Uint8Array(bytes.byteLength)
  copy.set(bytes)
  return copy.buffer
}

function cryptoApi(): Crypto {
  if (!globalThis.crypto?.subtle) throw new ManualSyncCryptoError('Web Crypto is unavailable in this browser')
  return globalThis.crypto
}

function assertPassphrase(passphrase: string) {
  if (passphrase.length < 12 || passphrase.length > 256) {
    throw new ManualSyncCryptoError('Sync passphrase must contain 12 to 256 characters')
  }
}

async function deriveKey(passphrase: string, salt: Uint8Array): Promise<CryptoKey> {
  const crypto = cryptoApi()
  const material = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(passphrase),
    'PBKDF2',
    false,
    ['deriveKey'],
  )
  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      hash: 'SHA-256',
      salt: toArrayBuffer(salt),
      iterations: KDF_ITERATIONS,
    },
    material,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

export function parseEncryptedManualSyncEnvelope(value: unknown): EncryptedManualSyncEnvelope {
  if (!value || typeof value !== 'object') throw new ManualSyncCryptoError('Invalid encrypted sync data')
  const envelope = value as Partial<EncryptedManualSyncEnvelope>
  if (
    envelope.type !== MANUAL_SYNC_ENVELOPE_TYPE ||
    envelope.version !== MANUAL_SYNC_VERSION ||
    envelope.kdf?.name !== 'PBKDF2' ||
    envelope.kdf.hash !== 'SHA-256' ||
    envelope.kdf.iterations !== KDF_ITERATIONS ||
    typeof envelope.kdf.salt !== 'string' ||
    envelope.cipher?.name !== 'AES-GCM' ||
    typeof envelope.cipher.iv !== 'string' ||
    typeof envelope.ciphertext !== 'string'
  ) {
    throw new ManualSyncCryptoError('Unsupported encrypted sync data')
  }
  if (
    envelope.kdf.salt.length > maximumBase64Length(SALT_LENGTH) ||
    envelope.cipher.iv.length > maximumBase64Length(IV_LENGTH) ||
    envelope.ciphertext.length > maximumBase64Length(MAX_MANUAL_SYNC_PLAINTEXT_BYTES + AES_GCM_TAG_LENGTH)
  ) {
    throw new ManualSyncCryptoError('Encrypted sync data is too large')
  }
  return envelope as EncryptedManualSyncEnvelope
}

export async function encryptManualSyncSnapshot(
  snapshot: ManualSyncSnapshot,
  passphrase: string,
): Promise<EncryptedManualSyncEnvelope> {
  assertPassphrase(passphrase)
  const crypto = cryptoApi()
  const canonicalSnapshot = parseManualSyncSnapshot(snapshot)
  const salt = crypto.getRandomValues(new Uint8Array(SALT_LENGTH))
  const iv = crypto.getRandomValues(new Uint8Array(IV_LENGTH))
  const plaintext = new TextEncoder().encode(JSON.stringify(canonicalSnapshot))
  if (plaintext.byteLength > MAX_MANUAL_SYNC_PLAINTEXT_BYTES) {
    throw new ManualSyncCryptoError('Sync data is too large to encrypt')
  }
  const key = await deriveKey(passphrase, salt)
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv: toArrayBuffer(iv), additionalData: ADDITIONAL_DATA },
    key,
    plaintext,
  )

  return {
    type: MANUAL_SYNC_ENVELOPE_TYPE,
    version: MANUAL_SYNC_VERSION,
    kdf: {
      name: 'PBKDF2',
      hash: 'SHA-256',
      iterations: KDF_ITERATIONS,
      salt: bytesToBase64(salt),
    },
    cipher: {
      name: 'AES-GCM',
      iv: bytesToBase64(iv),
    },
    ciphertext: bytesToBase64(new Uint8Array(ciphertext)),
  }
}

export async function decryptManualSyncSnapshot(
  value: unknown,
  passphrase: string,
): Promise<ManualSyncSnapshot> {
  assertPassphrase(passphrase)
  const envelope = parseEncryptedManualSyncEnvelope(value)
  const crypto = cryptoApi()
  const salt = base64ToBytes(envelope.kdf.salt, SALT_LENGTH)
  const iv = base64ToBytes(envelope.cipher.iv, IV_LENGTH)
  if (salt.length !== SALT_LENGTH || iv.length !== IV_LENGTH) {
    throw new ManualSyncCryptoError('Invalid encrypted sync data')
  }

  try {
    const key = await deriveKey(passphrase, salt)
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: toArrayBuffer(iv), additionalData: ADDITIONAL_DATA },
      key,
      toArrayBuffer(base64ToBytes(envelope.ciphertext, MAX_MANUAL_SYNC_PLAINTEXT_BYTES + AES_GCM_TAG_LENGTH)),
    )
    if (plaintext.byteLength > MAX_MANUAL_SYNC_PLAINTEXT_BYTES) {
      throw new ManualSyncCryptoError('Decrypted sync data is too large')
    }
    return parseManualSyncSnapshot(JSON.parse(new TextDecoder().decode(plaintext)))
  } catch (error) {
    if (error instanceof ManualSyncCryptoError) throw error
    throw new ManualSyncCryptoError('Unable to decrypt sync data. Check the passphrase.')
  }
}
