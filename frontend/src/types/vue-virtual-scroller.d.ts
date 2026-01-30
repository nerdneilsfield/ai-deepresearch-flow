declare module 'vue-virtual-scroller' {
  import type { DefineComponent } from 'vue'
  
  export const RecycleScroller: DefineComponent<{
    items: any[]
    itemSize: number
    keyField?: string
    buffer?: number
    class?: string
  }>
  
  export default {
    RecycleScroller
  }
}
