import { describe, expect, it } from 'vitest'
import { lazyKatexAuto, lazyMermaid, lazySaveAs, lazyZip } from '@/lib/lazy'

describe('lazy dependency loaders', () => {
  it('returns callable or usable values across CommonJS and ESM default shapes', async () => {
    const [Zip, saveAs, mermaid, renderMathInElement] = await Promise.all([
      lazyZip(),
      lazySaveAs(),
      lazyMermaid(),
      lazyKatexAuto(),
    ])

    expect(typeof Zip).toBe('function')
    expect(typeof saveAs).toBe('function')
    expect(typeof mermaid.render).toBe('function')
    expect(typeof renderMathInElement).toBe('function')
  })
})
