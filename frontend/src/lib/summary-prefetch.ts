import { getSummaryPayloadCached } from '@/lib/api'

const SUMMARY_PARTNERS: Record<string, string> = {
  deep_read: 'simple',
  simple: 'deep_read',
}

/** Fetch the alternate reading depth after its paired summary has been opened. */
export async function prefetchPairedSummary(
  paperId: string,
  template: string,
  summaryUrls: Record<string, string>,
): Promise<void> {
  const partner = SUMMARY_PARTNERS[template]
  const url = partner ? summaryUrls[partner] : undefined
  if (!partner || !url) return

  try {
    await getSummaryPayloadCached(paperId, partner, url)
  } catch {
    // Background cache warming must never affect the active summary.
  }
}
