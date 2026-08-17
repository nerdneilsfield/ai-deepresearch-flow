import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'

const translations: Record<string, string> = {
  helpTitle: 'Paper DB Guide',
  helpIntro: 'Search, triage, read, and export your paper collection from one place.',
  helpSummaryLine: 'A practical walkthrough of the pages, workflows, and reading modes that exist in the app today.',
  helpSectionQuickStart: 'Quick Start',
  helpSectionPages: 'Page Guide',
  helpSectionWorkflows: 'Common Workflows',
  helpSectionSync: 'Favorites and Secure Sync',
  helpSectionReading: 'Reading and Export',
  helpSectionTroubleshooting: 'Troubleshooting',
  helpQuickStartTitle: 'Quick Start',
  helpQuickStartIntro: 'Start broad, then narrow and save what matters.',
  helpPagesTitle: 'Page Guide',
  helpPagesIntro: 'Know which top-level page is for which job.',
  helpSearchPageTitle: 'Papers page',
  helpStatsPageTitle: 'Stats page',
  helpSelectedPageTitle: 'Selected page',
  helpFavoritesPageTitle: 'Favorites page',
  helpSyncPageTitle: 'Manual encrypted sync',
  helpSearchModesTitle: 'Search Modes',
  helpBasicSearchTitle: 'Basic search',
  helpAdvancedSearchTitle: 'Advanced search',
  helpAdvancedSearchDesc: 'Verify token before running advanced queries.',
  helpWorkflowTitle: 'Common Workflows',
  helpWorkflowIntro: 'Three reliable ways to use the app.',
  helpReadingTitle: 'Reading and Export',
  helpReadingIntro: 'Each mode is good at a different task.',
  helpTroubleshootingTitle: 'Troubleshooting',
  helpTroubleshootingIntro: 'Most confusion comes from a stale filter or the wrong view.',
  helpWorkflow2Title: 'Compare summary, markdown, and PDF',
  helpSyncTitle: 'Favorites and manual encrypted sync',
  helpSyncStepsTitle: 'Safe transfer path',
  helpSyncSafetyTitle: 'Safety boundaries',
  helpDetailPageTitle: 'Paper detail view',
  helpReadingMode4Title: 'Metadata, facets, and BibTeX',
  helpReadingMode1Title: 'Summaries and summary templates',
  helpReadingMode2Title: 'Markdown and PDF',
  helpReadingMode3Title: 'Split and Zen',
}

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => translations[key] ?? key,
  }),
}))

describe('HelpView', () => {
  it('renders the updated help structure and current workflow anchors', async () => {
    const { default: HelpView } = await import('@/views/HelpView.vue')
    const wrapper = mount(HelpView)

    expect(wrapper.find('[data-testid="help-hero"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="help-section"]')).toHaveLength(6)
    expect(wrapper.text()).toContain('Quick Start')
    expect(wrapper.text()).toContain('Page Guide')
    expect(wrapper.text()).toContain('Common Workflows')
    expect(wrapper.text()).toContain('Favorites and Secure Sync')
    expect(wrapper.text()).toContain('Favorites page')
    expect(wrapper.text()).toContain('Manual encrypted sync')
    expect(wrapper.text()).toContain('Safe transfer path')
    expect(wrapper.text()).toContain('Safety boundaries')
    expect(wrapper.text()).toContain('Search Modes')
    expect(wrapper.text()).toContain('Basic search')
    expect(wrapper.text()).toContain('Advanced search')
    expect(wrapper.text()).toContain('Paper detail view')
    expect(wrapper.text()).toContain('BibTeX')
    expect(wrapper.text()).toContain('summary')
    expect(wrapper.text()).toContain('PDF')
    expect(wrapper.text()).toContain('Split')
    expect(wrapper.text()).toContain('Zen')
    expect(wrapper.text()).toContain('Verify token')
    expect(wrapper.html()).toContain('href="#quick-start"')
    expect(wrapper.html()).toContain('href="#sync"')
    expect(wrapper.html()).toContain('href="#troubleshooting"')
  })
})
