import {
  P2P_ICE_CREDENTIAL_MAX_LENGTH,
  P2P_ICE_URL_MAX_LENGTH,
  P2P_ICE_USERNAME_MAX_LENGTH,
  P2P_MAX_ICE_SERVERS,
  P2P_MAX_ICE_URLS_PER_SERVER,
  type P2pIceServer,
  type StoredP2pIceServer,
} from '@/types/p2p-sync'

export class P2pIceConfigurationError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'P2pIceConfigurationError'
  }
}

function hasOnlyKnownProperties(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key))
}

function parseIceUrl(value: unknown): { url: string; kind: 'stun' | 'turn' } {
  if (typeof value !== 'string' || value.length === 0 || value.length > P2P_ICE_URL_MAX_LENGTH || value !== value.trim()) {
    throw new P2pIceConfigurationError('ICE server URLs must be trimmed strings no longer than 2048 characters')
  }
  if (/[^\x21-\x7e]/.test(value)) {
    throw new P2pIceConfigurationError('ICE server URLs cannot contain whitespace or control characters')
  }

  const match = /^(stun|stuns|turn|turns):([^/?#@]+)(?:\?transport=(udp|tcp))?$/i.exec(value)
  if (!match) {
    throw new P2pIceConfigurationError('ICE server URLs must use stun:, stuns:, turn:, or turns:')
  }
  const protocol = match[1]
  const hostAndPort = match[2]
  const transport = match[3]
  if (!protocol || !hostAndPort) {
    throw new P2pIceConfigurationError('ICE server URL is invalid')
  }
  if (protocol.toLowerCase().startsWith('stun') && transport) {
    throw new P2pIceConfigurationError('STUN server URLs cannot set a transport query')
  }
  const portMatch = /:(\d+)$/.exec(hostAndPort)
  if (portMatch && (Number(portMatch[1]) < 1 || Number(portMatch[1]) > 65_535)) {
    throw new P2pIceConfigurationError('ICE server port must be between 1 and 65535')
  }

  return {
    url: `${protocol.toLowerCase()}:${hostAndPort}${transport ? `?transport=${transport.toLowerCase()}` : ''}`,
    kind: protocol.toLowerCase().startsWith('stun') ? 'stun' : 'turn',
  }
}

function parseString(value: unknown, maximum: number, label: string, requireTrimmed = true): string | undefined {
  if (value === undefined) return undefined
  if (
    typeof value !== 'string' ||
    value.length === 0 ||
    value.length > maximum ||
    (requireTrimmed && value !== value.trim())
  ) {
    throw new P2pIceConfigurationError(`${label} must be a ${requireTrimmed ? 'trimmed ' : ''}string no longer than ${maximum} characters`)
  }
  return value
}

function parseIceServer(value: unknown, persistable = false): P2pIceServer {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new P2pIceConfigurationError('Each ICE server must be an object')
  }
  const server = value as Record<string, unknown>
  const allowed = persistable ? ['urls'] : ['urls', 'username', 'credential']
  if (!hasOnlyKnownProperties(server, allowed)) {
    throw new P2pIceConfigurationError('ICE server configuration contains unsupported properties')
  }

  const sourceUrls = typeof server.urls === 'string' ? [server.urls] : server.urls
  if (!Array.isArray(sourceUrls) || sourceUrls.length === 0 || sourceUrls.length > P2P_MAX_ICE_URLS_PER_SERVER) {
    throw new P2pIceConfigurationError(`Each ICE server must contain 1 to ${P2P_MAX_ICE_URLS_PER_SERVER} URLs`)
  }
  const parsedUrls = sourceUrls.map(parseIceUrl)
  const kinds = new Set(parsedUrls.map(({ kind }) => kind))
  if (kinds.size !== 1) {
    throw new P2pIceConfigurationError('Do not mix STUN and TURN URLs in one ICE server entry')
  }

  const username = parseString(server.username, P2P_ICE_USERNAME_MAX_LENGTH, 'TURN username')
  const credential = parseString(server.credential, P2P_ICE_CREDENTIAL_MAX_LENGTH, 'TURN credential', false)
  if (kinds.has('stun') && (username || credential)) {
    throw new P2pIceConfigurationError('STUN server entries cannot include TURN credentials')
  }
  if ((username === undefined) !== (credential === undefined)) {
    throw new P2pIceConfigurationError('TURN username and credential must be supplied together')
  }

  return {
    urls: parsedUrls.map(({ url }) => url),
    ...(username ? { username } : {}),
    ...(credential ? { credential } : {}),
  }
}

function parseIceServers(value: unknown, persistable = false): P2pIceServer[] {
  if (!Array.isArray(value) || value.length > P2P_MAX_ICE_SERVERS) {
    throw new P2pIceConfigurationError(`ICE configuration must be an array with at most ${P2P_MAX_ICE_SERVERS} servers`)
  }
  const servers = value.map((server) => parseIceServer(server, persistable))
  const urls = new Set<string>()
  for (const server of servers) {
    for (const url of server.urls) {
      if (urls.has(url)) throw new P2pIceConfigurationError('ICE server URLs must not be repeated')
      urls.add(url)
    }
  }
  return servers
}

export function parseP2pIceServers(text: string): P2pIceServer[] {
  if (typeof text !== 'string' || new TextEncoder().encode(text).byteLength > 64 * 1024) {
    throw new P2pIceConfigurationError('ICE configuration is too large')
  }
  if (!text.trim()) return []
  try {
    return parseIceServers(JSON.parse(text))
  } catch (error) {
    if (error instanceof P2pIceConfigurationError) throw error
    throw new P2pIceConfigurationError('ICE configuration must be valid JSON')
  }
}

export function stripP2pIceSecrets(servers: P2pIceServer[]): StoredP2pIceServer[] {
  return servers.map(({ urls }) => ({ urls: [...urls] }))
}

export function parseStoredP2pIceServers(value: unknown): StoredP2pIceServer[] | null {
  try {
    return parseIceServers(value, true).map(({ urls }) => ({ urls }))
  } catch {
    return null
  }
}

export function formatStoredP2pIceServers(servers: StoredP2pIceServer[]): string {
  return JSON.stringify(servers, null, 2)
}
