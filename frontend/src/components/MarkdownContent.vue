<script setup lang="ts">
import { ref, watch } from 'vue'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription, SheetTrigger } from '@/components/ui/sheet'
import { ArrowUp, List } from 'lucide-vue-next'
import RenderedMarkdown from './RenderedMarkdown.vue'
import type { OutlineItem } from '@/lib/outline'

const props = defineProps<{
  markdown: string
  imagesBaseUrl?: string | null
  class?: string
}>()

const outline = ref<OutlineItem[]>([])
const outlineOpen = ref(false)
const containerRef = ref<HTMLElement | null>(null)
// MdPreview always generates outline if headers exist
const outlineEligible = ref(true) 

function handleOutline(items: OutlineItem[]) {
  outline.value = items
  outlineEligible.value = items.length > 0
}

watch(
  () => props.markdown,
  (value) => {
    if (!value) outline.value = []
  }
)

function findScrollParent(el: HTMLElement | null) {
  let current: HTMLElement | null = el
  while (current && current !== document.body) {
    const style = window.getComputedStyle(current)
    if (/(auto|scroll)/.test(style.overflowY) && current.scrollHeight > current.clientHeight) {
      return current
    }
    current = current.parentElement
  }
  return null
}

function scrollToTop() {
  const root = containerRef.value
  const scrollParent = findScrollParent(root)
  if (scrollParent) {
    scrollParent.scrollTo({ top: 0, behavior: 'smooth' })
  } else {
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
}

function scrollToHeading(id: string) {
  const root = containerRef.value
  if (!root) return
  
  // MdPreview generates IDs. We need to match them.
  // Since we don't have the exact slug, we might try to find by text content if ID fails
  // But usually ID is close to text.
  // Let's try standard ID selector first
  let target = root.querySelector<HTMLElement>(`#${CSS.escape(id)}`)
  
  if (!target) {
     // Fallback: Try to find header with exact text
     const headers = root.querySelectorAll('h1, h2, h3, h4, h5, h6')
     for (const h of headers) {
       if (h.textContent?.trim() === id) {
         target = h as HTMLElement
         break
       }
     }
  }

  if (!target) return

  const scrollParent = findScrollParent(target)
  if (scrollParent) {
    const parentRect = scrollParent.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const offset = scrollParent.scrollTop + (targetRect.top - parentRect.top) - 12
    scrollParent.scrollTo({ top: offset, behavior: 'smooth' })
  } else {
    target.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }
}

function indentClass(level: number) {
  if (level <= 1) return 'pl-0'
  if (level === 2) return 'pl-3'
  if (level === 3) return 'pl-5'
  return 'pl-7'
}
</script>

<template>
  <div class="flex flex-col gap-3">
    <div ref="containerRef" class="relative">
      <RenderedMarkdown
        v-if="props.markdown"
        :markdown="props.markdown"
        :images-base-url="props.imagesBaseUrl || ''"
        :class="props.class"
        @outline="handleOutline"
      />
      <div v-else class="text-sm text-ink-500">No content available.</div>
      <div class="pointer-events-none sticky bottom-6 mt-4 flex justify-end">
        <div class="pointer-events-auto flex flex-col gap-2">
          <Sheet v-if="outline.length" v-model:open="outlineOpen">
            <SheetTrigger as-child>
              <Button size="icon" variant="outline" class="rounded-full shadow-sm" aria-label="Outline">
                <List />
              </Button>
            </SheetTrigger>
            <SheetContent side="right" class="w-72">
              <SheetHeader>
                <SheetTitle>Outline</SheetTitle>
                <SheetDescription class="sr-only">Table of contents</SheetDescription>
              </SheetHeader>
              <div v-if="outline.length" class="mt-4 space-y-1 text-sm">
                <button
                  v-for="item in outline"
                  :key="item.text"
                  type="button"
                  class="flex w-full items-start gap-2 rounded-md px-2 py-1 text-left text-ink-600 hover:bg-ink-50"
                  :class="indentClass(item.level)"
                  @click="scrollToHeading(item.text)"
                >
                  <span class="truncate">{{ item.text }}</span>
                </button>
              </div>
              <div v-else class="mt-4 text-sm text-ink-500">No outline available.</div>
            </SheetContent>
          </Sheet>
          <Button size="icon" variant="outline" class="rounded-full shadow-sm" aria-label="Back to top" @click="scrollToTop">
            <ArrowUp />
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>
