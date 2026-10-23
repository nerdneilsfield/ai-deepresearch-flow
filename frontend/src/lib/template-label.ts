// Summary template tags are internal ids (deep_read, simple…); show a readable name when one exists.
export function templateLabel(t: (key: string) => string, te: (key: string) => boolean, tag: string): string {
  const base = tag.replace(/_phi$/, '')
  return te(`templateName_${base}`) ? t(`templateName_${base}`) : tag
}

export function templateDescription(t: (key: string) => string, te: (key: string) => boolean, tag: string): string {
  const base = tag.replace(/_phi$/, '')
  return te(`templateDesc_${base}`) ? t(`templateDesc_${base}`) : ''
}
