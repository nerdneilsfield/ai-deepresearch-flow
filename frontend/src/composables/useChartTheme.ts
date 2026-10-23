import { computed } from 'vue'
import { useTheme } from './useTheme'

/**
 * ECharts theme bound to the app's CSS design tokens.
 *
 * All colors read from CSS variables (`--chart-*`, `--primary`, `--foreground`),
 * so they automatically match light/dark mode. Because the returned object
 * depends on `themeMode`, switching themes changes the object identity and
 * triggers Vue ECharts to re-evaluate its `:option` binding.
 *
 * Usage:
 *   const chartTheme = useChartTheme()
 *   // chartTheme.value.axis / .grid / .primary / .palette / .tooltip / .foreground
 */
export function useChartTheme() {
  const { isDark } = useTheme()

  // ECharts paints on canvas and cannot resolve `var(--x)`; read the token value now.
  const css = (name: string, alpha?: number) => {
    const value = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim()
    return alpha === undefined ? `hsl(${value})` : `hsl(${value} / ${alpha})`
  }

  // themeMode is read here so the computed re-evaluates on theme switch,
  // forcing dependent `:option` computeds to produce a new object and re-render.
  const theme = computed(() => {
    // touch the dependency; the dark class is already applied when isDark changes
    void isDark.value

    return {
      /** Axis label / tick color */
      axis: css('chart-axis'),
      /** Y-axis split line color */
      grid: css('chart-grid'),
      /** Primary single-series color (bars/lines) */
      primary: css('primary'),
      /** Default text/foreground color */
      foreground: css('foreground'),
      /** Tooltip background */
      tooltipBg: css('chart-tooltip-bg'),
      /** Tooltip text color */
      tooltipFg: css('chart-tooltip-fg'),
      /** Shared tooltip style object */
      tooltip: {
        backgroundColor: css('chart-tooltip-bg'),
        borderColor: 'transparent',
        textStyle: { color: css('chart-tooltip-fg') },
      },
      /** Categorical palette (navy → teal → cyan → amber → coral) */
      palette: [
        css('chart-1'),
        css('chart-2'),
        css('chart-3'),
        css('chart-4'),
        css('chart-5'),
      ],
      /**
       * Expanded palette (~11 colors) for pie charts with many slices.
       * Interpolates opacity across the 5-token palette so adjacent slices differ.
       */
      paletteLong: [
        css('chart-1'),
        css('chart-2'),
        css('chart-3'),
        css('chart-4'),
        css('chart-5'),
        css('chart-1', 0.75),
        css('chart-2', 0.75),
        css('chart-3', 0.75),
        css('chart-4', 0.75),
        css('chart-5', 0.75),
        css('chart-1', 0.5),
      ],
    }
  })

  return theme
}
