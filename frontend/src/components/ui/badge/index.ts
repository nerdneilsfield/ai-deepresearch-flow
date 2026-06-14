import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export { default as Badge } from "./Badge.vue"

export const badgeVariants = cva(
  "inline-flex gap-1 items-center rounded-md border px-2.5 py-0.5 text-xs font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2",
  {
    variants: {
      variant: {
        default:
          "border-transparent bg-primary text-primary-foreground shadow hover:bg-primary/80",
        secondary:
          "border-transparent bg-secondary text-secondary-foreground hover:bg-secondary/80",
        destructive:
          "border-transparent bg-destructive text-destructive-foreground shadow hover:bg-destructive/80",
        outline: "text-foreground",
        // Semantic colors — distilled from the MetadataPanel author/keyword/tag logic.
        // Each maps a resource concept to a stable hue across light & dark modes.
        navy:
          "border-accent-200 bg-accent-50 text-accent-700 hover:bg-accent-100 dark:border-accent-800 dark:bg-accent-950/40 dark:text-accent-300",
        teal:
          "border-sci-200 bg-sci-50 text-sci-700 hover:bg-sci-100 dark:border-sci-800 dark:bg-sci-950/40 dark:text-sci-300",
        slate:
          "border-ink-200 bg-ink-100 text-ink-600 hover:bg-ink-200 dark:border-ink-700 dark:bg-ink-800 dark:text-ink-300",
        amber:
          "border-amber-200 bg-amber-50 text-amber-700 hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-300",
        violet:
          "border-violet-200 bg-violet-50 text-violet-700 hover:bg-violet-100 dark:border-violet-800 dark:bg-violet-950/40 dark:text-violet-300",
        // PDF — red is the universal document/PDF convention
        pdf:
          "border-red-200 bg-red-50 text-red-600 hover:bg-red-100 dark:border-red-900 dark:bg-red-950/40 dark:text-red-300",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
)

export type BadgeVariants = VariantProps<typeof badgeVariants>
