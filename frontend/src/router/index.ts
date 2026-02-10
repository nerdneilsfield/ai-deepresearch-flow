import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      name: 'search',
      component: () => import('../views/SearchView.vue'),
    },
    {
      path: '/paper/:paperId',
      name: 'paper',
      component: () => import('../views/PaperDetailView.vue'),
    },
    {
      path: '/selected',
      name: 'selected',
      component: () => import('../views/SelectedView.vue'),
    },
    {
      path: '/stats',
      name: 'stats',
      component: () => import('../views/StatsView.vue'),
    },
    {
      path: '/help',
      name: 'help',
      component: () => import('../views/HelpView.vue'),
    },
    {
      path: '/stats/:facet/:value',
      name: 'facet-stats',
      component: () => import('../views/FacetStatsView.vue'),
    },
    {
      path: '/facet/:facet/:value',
      name: 'facet',
      component: () => import('../views/FacetStatsView.vue'),
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

export default router
