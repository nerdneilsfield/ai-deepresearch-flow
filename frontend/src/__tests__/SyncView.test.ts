import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SearchItem } from '@/types/api'
import type { DownloadedManualSync, WebDavSyncSettings } from '@/types/manual-sync'

const { selectionState, favoritesState, syncState, pushToastMock } = vi.hoisted(() => ({
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
  syncState: {
    settings: null as WebDavSyncSettings | null,
    metadata: null,
    pending: null as DownloadedManualSync | null,
    remoteConflict: null,
    busyAction: null,
    isConfigured: true,
    pendingIsOlderThanAcknowledged: false,
    init: vi.fn(),
    saveSettings: vi.fn(),
    forgetSettings: vi.fn(),
    upload: vi.fn(),
    download: vi.fn(),
    acceptPendingDownload: vi.fn(),
    dismissPendingDownload: vi.fn(),
  },
  pushToastMock: vi.fn(),
}))

vi.mock('@/stores/selection', () => ({ useSelectionStore: () => selectionState }))
vi.mock('@/stores/favorites', () => ({ useFavoriteStore: () => favoritesState }))
vi.mock('@/stores/manual-sync', () => ({ useManualSyncStore: () => syncState }))
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

async function mountView() {
  const { default: SyncView } = await import('@/views/SyncView.vue')
  return shallowMount(SyncView, {
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
  favoritesState.items = []
  favoritesState.init.mockReset()
  favoritesState.init.mockResolvedValue(undefined)
  favoritesState.merge.mockReset()
  favoritesState.merge.mockResolvedValue(1)
  favoritesState.replace.mockReset()
  syncState.settings = {
    provider: 'webdav',
    endpoint: 'https://cloud.example/paperdb.sync',
    username: 'ada',
    updatedAt: 1,
  }
  syncState.metadata = null
  syncState.pending = null
  syncState.remoteConflict = null
  syncState.busyAction = null
  syncState.isConfigured = true
  syncState.pendingIsOlderThanAcknowledged = false
  syncState.init.mockReset()
  syncState.init.mockResolvedValue(undefined)
  syncState.saveSettings.mockReset()
  syncState.forgetSettings.mockReset()
  syncState.upload.mockReset()
  syncState.download.mockReset()
  syncState.acceptPendingDownload.mockReset()
  syncState.acceptPendingDownload.mockResolvedValue(undefined)
  syncState.dismissPendingDownload.mockReset()
  pushToastMock.mockReset()
})

describe('SyncView', () => {
  it('does not transfer data while the page is merely opened', async () => {
    const wrapper = await mountView()
    await settle(wrapper)

    expect(syncState.init).toHaveBeenCalled()
    expect(syncState.upload).not.toHaveBeenCalled()
    expect(syncState.download).not.toHaveBeenCalled()
  })

  it('keeps a downloaded snapshot pending until the user explicitly selects merge', async () => {
    const selected = makePaper('remote-selected')
    const favorite = {
      paper: makePaper('remote-favorite'),
      rating: 5 as const,
      createdAt: 1,
      updatedAt: 2,
    }
    syncState.pending = {
      snapshot: {
        type: 'paperdb-manual-sync',
        version: 1,
        createdAt: 3,
        selection: [selected],
        favorites: [favorite],
      },
      remote: { endpoint: 'https://cloud.example/paperdb.sync', exists: true, etag: '"v1"' },
    }
    const wrapper = await mountView()
    await settle(wrapper)

    expect(wrapper.find('[data-testid="sync-pending-download"]').exists()).toBe(true)
    expect(selectionState.merge).not.toHaveBeenCalled()
    expect(favoritesState.merge).not.toHaveBeenCalled()

    const mergeButton = wrapper.findAll('button').find((button) => button.text().includes('syncMergeDownloaded'))
    await mergeButton!.trigger('click')
    await settle(wrapper)

    expect(selectionState.merge).toHaveBeenCalledWith([selected])
    expect(favoritesState.merge).toHaveBeenCalledWith([favorite])
    expect(syncState.acceptPendingDownload).toHaveBeenCalled()
  })

  it('requires a second explicit decision before applying an older authenticated snapshot', async () => {
    const selected = makePaper('older-selected')
    syncState.pending = {
      snapshot: {
        type: 'paperdb-manual-sync',
        version: 1,
        createdAt: 1,
        selection: [selected],
        favorites: [],
      },
      remote: { endpoint: 'https://cloud.example/paperdb.sync', exists: true, etag: '"v1"' },
    }
    syncState.pendingIsOlderThanAcknowledged = true
    const wrapper = await mountView()
    await settle(wrapper)

    expect(wrapper.find('[data-testid="sync-older-snapshot-warning"]').exists()).toBe(true)
    const mergeButton = wrapper.findAll('button').find((button) => button.text().includes('syncMergeDownloaded'))
    await mergeButton!.trigger('click')
    await settle(wrapper)
    expect(selectionState.merge).not.toHaveBeenCalled()

    const confirmButton = wrapper.findAll('button').find((button) => button.text().includes('syncConfirmOlderSnapshot'))
    await confirmButton!.trigger('click')
    await settle(wrapper)
    expect(selectionState.merge).toHaveBeenCalledWith([selected])
  })
})
