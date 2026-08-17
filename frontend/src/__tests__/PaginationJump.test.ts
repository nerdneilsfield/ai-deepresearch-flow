import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import PaginationJump from '@/components/ui/pagination/PaginationJump.vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => ({ jumpToPage: 'Go to page', go: 'Go' })[key] ?? key,
  }),
}))

function mountJump(modelValue = 2, totalPages = 10) {
  return mount(PaginationJump, {
    props: { modelValue, totalPages },
  })
}

describe('PaginationJump', () => {
  it('emits a requested in-range page', async () => {
    const wrapper = mountJump()

    await wrapper.get('[data-testid="pagination-jump-input"]').setValue('7')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('update:modelValue')).toEqual([[7]])
  })

  it('clamps a requested page to the available range', async () => {
    const wrapper = mountJump(2, 10)

    await wrapper.get('[data-testid="pagination-jump-input"]').setValue('99')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('update:modelValue')).toEqual([[10]])
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('10')
  })

  it('clamps a too-small page to the first page', async () => {
    const wrapper = mountJump(4, 10)

    await wrapper.get('[data-testid="pagination-jump-input"]').setValue('0')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('update:modelValue')).toEqual([[1]])
  })

  it('restores the current page for an invalid value', async () => {
    const wrapper = mountJump(4, 10)

    await wrapper.get('[data-testid="pagination-jump-input"]').setValue('2.5')
    await wrapper.get('form').trigger('submit')

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
    expect((wrapper.get('input').element as HTMLInputElement).value).toBe('4')
  })
})
