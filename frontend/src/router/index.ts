import { createRouter, createWebHistory } from 'vue-router'
import SearchView from '../views/SearchView.vue'
import PaperDetailView from '../views/PaperDetailView.vue'
import SelectedView from '../views/SelectedView.vue'
import StatsView from '../views/StatsView.vue'
import FacetStatsView from '../views/FacetStatsView.vue'
import HelpView from '../views/HelpView.vue'
import { useRuntimeConfigStore } from '@/stores/runtime-config'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'search',
      component: SearchView,
    },
    {
      path: '/paper/:paperId',
      name: 'paper',
      component: PaperDetailView,
    },
    {
      path: '/selected',
      name: 'selected',
      component: SelectedView,
    },
    {
      path: '/stats',
      name: 'stats',
      component: StatsView,
    },
    {
      path: '/help',
      name: 'help',
      component: HelpView,
    },
    {
      path: '/stats/:facet/:value',
      name: 'facet-stats',
      component: FacetStatsView,
    },
    {
      path: '/facet/:facet/:value',
      name: 'facet',
      component: FacetStatsView,
    }
  ],
  scrollBehavior(_to, _from, savedPosition) {
    if (savedPosition) {
      return savedPosition
    } else {
      return { top: 0 }
    }
  },
})

router.beforeEach(async (_to, _from) => {
  const runtimeConfig = useRuntimeConfigStore()
  await runtimeConfig.load()
})

export default router
