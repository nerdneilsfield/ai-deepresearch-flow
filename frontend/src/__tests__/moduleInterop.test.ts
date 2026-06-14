import { describe, expect, it } from 'vitest'
import { resolveCallableExport, resolveDefaultExport, resolveMarkdownItPlugin } from '@/lib/module-interop'

describe('module interop helpers', () => {
  it('returns the usable default export without unwrapping callable exports again', () => {
    const fn = () => 'ok'
    ;(fn as { default?: string }).default = 'metadata'

    expect(resolveDefaultExport({ default: fn })).toBe(fn)
  })

  it('resolves callable exports from direct, default, and nested default module shapes', () => {
    const direct = () => 'direct'
    const single = () => 'single'
    const nested = () => 'nested'

    expect(resolveCallableExport(direct)).toBe(direct)
    expect(resolveCallableExport({ default: single })).toBe(single)
    expect(resolveCallableExport({ default: { default: nested } })).toBe(nested)
  })

  it('rejects module shapes that do not expose a callable plugin', () => {
    expect(() => resolveMarkdownItPlugin({ default: { notCallable: true } })).toThrow(/not callable/)
  })
})
