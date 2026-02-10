import { z } from 'zod'

const StringOrNumber = z.union([z.string(), z.number()]).transform((value) => String(value))

export const SearchItemSchema = z
  .object({
    paper_id: z.string(),
    paper_index: z.number().optional(),
    title: z.string().default(''),
    year: StringOrNumber.default(''),
    venue: z.string().default(''),
    authors: z.array(z.string()).optional().default([]),
    summary_preview: z.string().optional(),
    snippet_markdown: z.string().optional(),
    preferred_summary_template: z.string().optional(),
    has_pdf: z.boolean().optional(),
    has_source: z.boolean().optional(),
    has_translated: z.boolean().optional(),
    pdf_url: z.string().nullable().optional(),
    source_md_url: z.string().nullable().optional(),
    translated_md_urls: z.record(z.string()).optional(),
    images_base_url: z.string().optional(),
    summary_url: z.string().optional(),
    manifest_url: z.string().optional(),
  })
  .passthrough()

export const SearchResponseSchema = z
  .object({
    page: z.number(),
    page_size: z.number(),
    total: z.number(),
    has_more: z.boolean(),
    items: z.array(SearchItemSchema),
  })
  .passthrough()

export const FacetItemSchema = z
  .object({
    id: StringOrNumber,
    value: StringOrNumber,
    paper_count: z.number(),
  })
  .passthrough()

export const FacetResponseSchema = z
  .object({
    page: z.number(),
    page_size: z.number(),
    total: z.number(),
    has_more: z.boolean(),
    items: z.array(FacetItemSchema),
  })
  .passthrough()

export const StatsBucketSchema = z
  .object({
    value: StringOrNumber,
    paper_count: z.number(),
  })
  .passthrough()

export const StatsResponseSchema = z
  .object({
    total: z.number(),
    years: z.array(StatsBucketSchema),
    months: z.array(StatsBucketSchema),
    authors: z.array(StatsBucketSchema),
    venues: z.array(StatsBucketSchema),
    institutions: z.array(StatsBucketSchema),
    keywords: z.array(StatsBucketSchema),
    tags: z.array(StatsBucketSchema),
  })
  .passthrough()

export const PaperDetailSchema = z
  .object({
    paper_id: z.string(),
    title: z.string(),
    year: StringOrNumber,
    venue: z.string(),
    doi: z.string().nullable().optional(),
    authors: z.array(z.string()).default([]),
    keywords: z.array(z.string()).default([]),
    institutions: z.array(z.string()).default([]),
    tags: z.array(z.string()).default([]),
    output_language: z.string().optional(),
    provider: z.string().optional(),
    model: z.string().optional(),
    prompt_template: z.string().optional(),
    preferred_summary_template: z.string().optional(),
    summary_urls: z.record(z.string()).optional(),
    pdf_url: z.string().nullable().optional(),
    source_md_url: z.string().nullable().optional(),
    translated_md_urls: z.record(z.string()).optional(),
    images_base_url: z.string().optional(),
    summary_url: z.string().optional(),
    manifest_url: z.string().optional(),
  })
  .passthrough()

export const ManifestAssetSchema = z
  .object({
    static_path: z.string().nullable().optional(),
    zip_path: z.string().nullable().optional(),
    sha256: z.string().nullable().optional(),
    template_tag: z.string().optional(),
  })
  .passthrough()

export const ManifestSchema = z
  .object({
    paper_id: z.string(),
    folder_name: z.string(),
    folder_name_short: z.string(),
    assets: z
      .object({
        pdf: ManifestAssetSchema.optional(),
        source_md: ManifestAssetSchema.optional(),
        translated_md: z.array(ManifestAssetSchema.extend({ lang: z.string() })).optional(),
        summary: ManifestAssetSchema.optional(),
        summary_templates: z
          .array(ManifestAssetSchema.extend({ template_tag: z.string() }))
          .optional(),
      })
      .passthrough(),
    images: z.array(ManifestAssetSchema.extend({ ext: z.string().optional(), status: z.string().optional() })),
  })
  .passthrough()

export const PaperBibtexSchema = z
  .object({
    paper_id: z.string(),
    doi: z.string().nullable().optional(),
    bibtex_raw: z.string(),
    bibtex_key: z.string().nullable().optional(),
    entry_type: z.string().nullable().optional(),
  })
  .passthrough()

export const FacetStatsResponseSchema = z
  .object({
    facet_type: z.string(),
    value: z.string(),
    total: z.number(),
    related: z.record(z.array(StatsBucketSchema)),
  })
  .passthrough()

export type SearchItem = z.infer<typeof SearchItemSchema>
export type SearchResponse = z.infer<typeof SearchResponseSchema>
export type FacetItem = z.infer<typeof FacetItemSchema>
export type FacetResponse = z.infer<typeof FacetResponseSchema>
export type StatsResponse = z.infer<typeof StatsResponseSchema>
export type PaperDetail = z.infer<typeof PaperDetailSchema>
export type Manifest = z.infer<typeof ManifestSchema>
export type PaperBibtex = z.infer<typeof PaperBibtexSchema>
export type FacetStatsResponse = z.infer<typeof FacetStatsResponseSchema>
