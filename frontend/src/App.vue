<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { setLocale } from '@/i18n'
import { useSelectionStore } from '@/stores/selection'
import { useUiStore } from '@/stores/ui'
// Toaster component imported but not directly used in template (used by toast function)
// @ts-expect-error - Toaster is used by the toast utility function
import { Toaster } from '@/components/ui/toast'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Sheet, SheetContent, SheetTrigger, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { Button } from '@/components/ui/button'
import ErrorBoundary from '@/components/ErrorBoundary.vue'
import { useOnline, useWindowScroll } from '@vueuse/core'
import { Github, ArrowUp } from 'lucide-vue-next'

const route = useRoute()
const router = useRouter()
const { locale, t } = useI18n()
const selection = useSelectionStore()
const ui = useUiStore()
const isOnline = useOnline()
const { y: scrollY } = useWindowScroll()

const activeRoute = computed(() => String(route.name || ''))
const isDetail = computed(() => activeRoute.value === 'paper')
const detailTitle = computed(() => ui.detailTitle)
const detailSubtitle = computed(() => ui.detailSubtitle)
const year = new Date().getFullYear()
const version = import.meta.env.VITE_APP_VERSION

function switchLanguage(value: string) {
  setLocale(value)
}

function goto(path: string) {
  router.push(path)
}

function scrollToTop() {
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

// Initialize selection store from IndexedDB on app start
onMounted(() => {
  selection.init()
})
</script>

<template>
  <div class="flex min-h-screen flex-col">
    <header class="sticky top-0 z-50 border-b border-ink-100 bg-white/80 backdrop-blur-md">
      <div class="mx-auto grid w-full max-w-6xl grid-cols-[auto_1fr_auto] items-center gap-4 px-4 py-3 md:grid-cols-[1fr_auto_1fr]">
        <div class="flex items-center gap-4">
          <button class="text-base font-semibold text-ink-900 shrink-0" type="button" @click="goto('/')">
            {{ t('appTitle') }}
          </button>
          <nav class="hidden items-center gap-4 text-sm text-ink-500 md:flex">
            <button
              class="transition hover:text-ink-900"
              :class="{ 'text-ink-900 font-semibold': activeRoute === 'search' }"
              :aria-current="activeRoute === 'search' ? 'page' : undefined"
              type="button"
              @click="goto('/')"
            >
              {{ t('papers') }}
            </button>
            <button
              class="transition hover:text-ink-900"
              :class="{ 'text-ink-900 font-semibold': activeRoute === 'stats' }"
              :aria-current="activeRoute === 'stats' ? 'page' : undefined"
              type="button"
              @click="goto('/stats')"
            >
              {{ t('stats') }}
            </button>
            <button
              class="transition hover:text-ink-900"
              :class="{ 'text-ink-900 font-semibold': activeRoute === 'selected' }"
              :aria-current="activeRoute === 'selected' ? 'page' : undefined"
              type="button"
              @click="goto('/selected')"
            >
              {{ t('selected', { count: selection.count }) }}
            </button>
            <button
              class="transition hover:text-ink-900"
              :class="{ 'text-ink-900 font-semibold': activeRoute === 'help' }"
              :aria-current="activeRoute === 'help' ? 'page' : undefined"
              type="button"
              @click="goto('/help')"
            >
              {{ t('help') }}
            </button>
          </nav>
        </div>

        <div v-if="isDetail && detailTitle" class="flex max-w-[40vw] flex-col items-center px-2 text-center md:max-w-[48vw]">
          <div class="line-clamp-2 text-xs font-semibold leading-tight text-ink-900 md:text-sm">{{ detailTitle }}</div>
          <div v-if="detailSubtitle" class="line-clamp-1 text-[10px] text-ink-500 md:text-xs">{{ detailSubtitle }}</div>
        </div>
        <div v-else class="hidden md:block"></div>

        <div class="flex items-center justify-end gap-2 text-sm">
          <DropdownMenu>
            <DropdownMenuTrigger as-child>
              <Button variant="outline" size="sm" aria-label="Switch language">
                {{ locale === 'en' ? 'EN' : '中文' }}
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem @click="switchLanguage('en')">English</DropdownMenuItem>
              <DropdownMenuItem @click="switchLanguage('zh')">中文</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          
          <Sheet>
            <SheetTrigger class="md:hidden" aria-label="Open navigation menu" as-child>
              <Button variant="outline" size="sm">{{ t('menu') }}</Button>
            </SheetTrigger>
            <SheetContent side="right" class="space-y-4">
              <SheetTitle>{{ t('appTitle') }}</SheetTitle>
              <SheetDescription class="sr-only">Main Navigation</SheetDescription>
              <nav class="flex flex-col gap-2 text-sm">
                <Button variant="ghost" @click="goto('/')">{{ t('papers') }}</Button>
                <Button variant="ghost" @click="goto('/stats')">{{ t('stats') }}</Button>
                <Button variant="ghost" @click="goto('/selected')">{{ t('selected', { count: selection.count }) }}</Button>
                <Button variant="ghost" @click="goto('/help')">{{ t('help') }}</Button>
              </nav>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
    <div v-if="!isOnline" class="border-b border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
      You are offline. Some data may be unavailable.
    </div>

    <main
      :class="[
        'mx-auto w-full flex-1 px-4',
        isDetail ? 'max-w-none py-6' : 'max-w-6xl py-8',
      ]"
    >
      <ErrorBoundary>
        <router-view />
      </ErrorBoundary>
    </main>

    <footer class="border-t border-ink-100 bg-white py-6 text-sm text-ink-500">
      <div class="mx-auto flex max-w-6xl flex-col items-center justify-between gap-4 px-4 sm:flex-row">
        <div class="flex items-center gap-4">
          <span>&copy; {{ year }} ai-deepresearch-flow</span>
          <span class="hidden text-ink-200 sm:inline">|</span>
          <span>v{{ version }}</span>
        </div>
        <div class="flex items-center gap-4">
          <a
            href="https://github.com/nerdneilsfield/ai-deepresearch-flow"
            target="_blank"
            rel="noopener noreferrer"
            class="flex items-center gap-2 transition hover:text-ink-900"
          >
            <Github class="h-4 w-4" />
            <span>GitHub</span>
          </a>
        </div>
      </div>
    </footer>

    <transition
      enter-active-class="transition duration-300 ease-out"
      enter-from-class="translate-y-10 opacity-0"
      enter-to-class="translate-y-0 opacity-100"
      leave-active-class="transition duration-200 ease-in"
      leave-from-class="translate-y-0 opacity-100"
      leave-to-class="translate-y-10 opacity-0"
    >
      <Button
        v-if="scrollY > 300"
        size="icon"
        class="fixed bottom-6 right-6 z-40 rounded-full shadow-lg md:bottom-10 md:right-10"
        @click="scrollToTop"
        aria-label="Back to top"
      >
        <ArrowUp class="h-5 w-5" />
      </Button>
    </transition>
  </div>
</template>
