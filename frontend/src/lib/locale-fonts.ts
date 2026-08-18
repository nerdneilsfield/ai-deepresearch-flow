let chineseFontLoad: Promise<void> | null = null

/** Load the large CJK font subset only for a Chinese interface. */
export function preloadLocaleFonts(locale: string): void {
  if (!locale.toLowerCase().startsWith('zh') || chineseFontLoad) return

  chineseFontLoad = Promise.all([
    import('@fontsource/noto-sans-sc/400.css'),
    import('@fontsource/noto-sans-sc/500.css'),
    import('@fontsource/noto-sans-sc/700.css'),
  ]).then(
    () => undefined,
    () => undefined,
  )
}
