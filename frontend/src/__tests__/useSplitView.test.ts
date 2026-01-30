import { describe, expect, it } from 'vitest'
import { useSplitView } from '@/composables/useSplitView'

describe('useSplitView', () => {
  it('swaps left and right views', () => {
    const state = useSplitView()
    state.leftView.value = 'summary'
    state.rightView.value = 'pdf'
    state.swapSplit()
    expect(state.leftView.value).toBe('pdf')
    expect(state.rightView.value).toBe('summary')
  })

  it('adjusts split percent within bounds', () => {
    const state = useSplitView()
    state.splitPercent.value = 50
    state.widenLeft()
    expect(state.splitPercent.value).toBe(55)
    state.tightenLeft()
    state.tightenLeft()
    expect(state.splitPercent.value).toBe(45)
  })
})
