# Change: Add PDF.js viewer iframe mode and dual-pane split view for db serve

## Why
Users want a PDF viewing option with the full PDF.js viewer UI (search, thumbnails, etc.) alongside the existing in-page canvas renderer.
They also want a dual-pane layout so two views can be opened side-by-side (e.g., summary + PDF viewer) with independent scrolling.
To make the PDF.js viewer work reliably with local PDF APIs, the app must serve the viewer assets locally (avoid cross-origin/CORS issues).
Split view should persist state in the URL so it survives refreshes and can be shared.
Summary view should include a collapsible table-of-contents panel and a quick "back to top" control for long documents.

## What Changes
- Add a new PDF view mode that embeds the PDF.js viewer in an iframe from the paper detail page.
- Expose the new view in the open-view selector and detail tabs.
- Add a dual-pane split view that lets users choose what to show on the left and right panes.
- Render split panes as iframes to allow independent scrolling for each pane content.
- Serve the PDF.js viewer assets locally and embed the local viewer in an iframe.
- Keep split view state (left/right selections) in URL query parameters for deep linking.
- Add responsive behavior for narrow screens (stack panes or show one pane with a switcher).
- Add a collapsible summary outline panel anchored at the top-left of the summary view.
- Add a bottom-left "back to top" control in summary view.
- Document the new PDF viewer mode in README usage.

## Impact
- Affected specs: paper
- Affected code: python/deepresearch_flow/paper/web/app.py, README.md
