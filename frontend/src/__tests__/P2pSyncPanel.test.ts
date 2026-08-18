import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SearchItem } from '@/types/api'
import type { P2pPendingSnapshot } from '@/types/p2p-sync'

const { selectionState, favoritesState, p2pState, pushToastMock } = vi.hoisted(() => ({
  selectionState: {
    items: [] as SearchItem[],
    init: vi.fn(),
    merge: vi.fn(),
    replace: vi.fn(),
  },
  favoritesState: {
    items: [],
    init: vi.fn(),
    merge: vi.fn(),
    replace: vi.fn(),
  },
  p2pState: {
    iceServers: [],
    role: null as 'offer' | 'answer' | null,
    localSignal: null as string | null,
    connectionState: 'idle',
    channelOpen: false,
    isConnected: false,
    receivedEnvelope: null,
    pending: null as P2pPendingSnapshot | null,
    busyAction: null,
    lastError: null,
    pendingIsOlderThanAccepted: false,
    init: vi.fn(),
    saveIceServers: vi.fn(),
    createOffer: vi.fn(),
    acceptOffer: vi.fn(),
    acceptAnswer: vi.fn(),
    closeSession: vi.fn(),
    send: vi.fn(),
    decryptReceived: vi.fn(),
    dismissReceived: vi.fn(),
    acceptPending: vi.fn(),
    dismissPending: vi.fn(),
  },
  pushToastMock: vi.fn(),
}))

vi.mock('@/stores/selection', () => ({ useSelectionStore: () => selectionState }))
vi.mock('@/stores/favorites', () => ({ useFavoriteStore: () => favoritesState }))
vi.mock('@/stores/p2p-sync', () => ({ useP2pSyncStore: () => p2pState }))
vi.mock('@/stores/ui', () => ({ useUiStore: () => ({ pushToast: pushToastMock }) }))
vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string, params?: Record<string, unknown>) => `${key}${params ? JSON.stringify(params) : ''}` }),
}))

function makePaper(paperId: string): SearchItem {
  return {
    paper_id: paperId,
    title: `Paper ${paperId}`,
    year: '2026',
    venue: 'ICLR',
    authors: ['Ada'],
  }
}

async function settle(wrapper: ReturnType<typeof shallowMount>) {
  await flushPromises()
  await wrapper.vm.$nextTick()
}

async function mountPanel() {
  const { default: P2pSyncPanel } = await import('@/components/sync/P2pSyncPanel.vue')
  return shallowMount(P2pSyncPanel, {
    global: {
      stubs: {
        Button: { emits: ['click'], template: '<button v-bind="$attrs" @click="$emit(\'click\')"><slot /></button>' },
      },
    },
  })
}

beforeEach(() => {
  selectionState.items = []
  selectionState.init.mockReset()
  selectionState.init.mockResolvedValue(undefined)
  selectionState.merge.mockReset()
  selectionState.merge.mockResolvedValue(1)
  selectionState.replace.mockReset()
  selectionState.replace.mockResolvedValue(1)
  favoritesState.items = []
  favoritesState.init.mockReset()
  favoritesState.init.mockResolvedValue(undefined)
  favoritesState.merge.mockReset()
  favoritesState.merge.mockResolvedValue(1)
  favoritesState.replace.mockReset()
  favoritesState.replace.mockResolvedValue(1)
  p2pState.iceServers = []
  p2pState.role = null
  p2pState.localSignal = null
  p2pState.connectionState = 'idle'
  p2pState.channelOpen = false
  p2pState.isConnected = false
  p2pState.receivedEnvelope = null
  p2pState.pending = null
  p2pState.busyAction = null
  p2pState.lastError = null
  p2pState.pendingIsOlderThanAccepted = false
  p2pState.init.mockReset()
  p2pState.init.mockResolvedValue(undefined)
  p2pState.saveIceServers.mockReset()
  p2pState.saveIceServers.mockResolvedValue([])
  p2pState.createOffer.mockReset()
  p2pState.createOffer.mockResolvedValue('offer')
  p2pState.acceptOffer.mockReset()
  p2pState.acceptOffer.mockResolvedValue('answer')
  p2pState.acceptAnswer.mockReset()
  p2pState.acceptAnswer.mockResolvedValue(undefined)
  p2pState.closeSession.mockReset()
  p2pState.send.mockReset()
  p2pState.decryptReceived.mockReset()
  p2pState.dismissReceived.mockReset()
  p2pState.acceptPending.mockReset()
  p2pState.acceptPending.mockResolvedValue(undefined)
  p2pState.dismissPending.mockReset()
  pushToastMock.mockReset()
})

describe('P2pSyncPanel', () => {
  it('passes custom STUN and TURN configuration to the explicit offer action', async () => {
    const wrapper = await mountPanel()
    await settle(wrapper)
    const iceJson = JSON.stringify([
      { urls: ['stun:stun.example.test:3478'] },
      { urls: ['turn:turn.example.test:3478?transport=udp'], username: 'ada', credential: 'secret' },
    ])

    await wrapper.find('[data-testid="p2p-ice-servers"]').setValue(iceJson)
    const offerButton = wrapper.findAll('button').find((button) => button.text().includes('p2pCreateOffer'))
    await offerButton!.trigger('click')
    await settle(wrapper)

    expect(p2pState.createOffer).toHaveBeenCalledWith(iceJson)
  })

  it('keeps a decrypted P2P snapshot pending until the user explicitly selects merge', async () => {
    const selected = makePaper('remote-selected')
    const favorite = {
      paper: makePaper('remote-favorite'),
      rating: 5 as const,
      createdAt: 1,
      updatedAt: 2,
    }
    p2pState.pending = {
      snapshot: {
        type: 'paperdb-manual-sync',
        version: 1,
        createdAt: 3,
        selection: [selected],
        favorites: [favorite],
      },
      receivedAt: 4,
    }
    const wrapper = await mountPanel()
    await settle(wrapper)

    expect(wrapper.find('[data-testid="p2p-pending-snapshot"]').exists()).toBe(true)
    expect(selectionState.merge).not.toHaveBeenCalled()

    const mergeButton = wrapper.findAll('button').find((button) => button.text().includes('syncMergeDownloaded'))
    await mergeButton!.trigger('click')
    await settle(wrapper)

    expect(selectionState.merge).toHaveBeenCalledWith([selected])
    expect(favoritesState.merge).toHaveBeenCalledWith([favorite])
    expect(p2pState.acceptPending).toHaveBeenCalled()
  })
})
