import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import FavoriteRatingControl from '@/components/favorites/FavoriteRatingControl.vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => `${key}${params?.rating ?? ''}`,
  }),
}))

describe('FavoriteRatingControl', () => {
  it('emits separate favorite, rating, and removal actions', async () => {
    const wrapper = mount(FavoriteRatingControl, {
      props: { isFavorite: false },
    })

    await wrapper.find('[data-testid="favorite-toggle"]').trigger('click')
    expect(wrapper.emitted('toggleFavorite')).toHaveLength(1)

    await wrapper.setProps({ isFavorite: true, rating: 3 })
    await wrapper.find('[data-testid="favorite-rating-2"]').trigger('click')
    expect(wrapper.emitted('setRating')).toEqual([[2]])

    await wrapper.find('[data-testid="favorite-toggle"]').trigger('click')
    expect(wrapper.emitted('toggleFavorite')).toHaveLength(2)
  })
})
