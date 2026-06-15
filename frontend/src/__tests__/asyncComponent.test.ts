import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { defineSafeAsyncComponent } from '@/lib/async-component'

describe('defineSafeAsyncComponent', () => {
  it('renders a local fallback when an async component loader fails', async () => {
    const Broken = defineSafeAsyncComponent('Markdown panel', async () => {
      throw new Error('chunk unavailable')
    })
    const wrapper = mount(Broken)

    await new Promise((resolve) => setTimeout(resolve, 0))
    await nextTick()

    expect(wrapper.text()).toContain('Markdown panel is unavailable')
    expect(wrapper.text()).toContain('chunk unavailable')
  })
})
