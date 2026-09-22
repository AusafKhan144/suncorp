# Captured NRMA journeys — keep these

`.har` is a copy-as-cURL capture of a real Chrome journey through the NRMA
quote flow. Diffing our requests against it is what solved the 403 wall on
2026-09-09 (wrong referer, missing trace headers, a skipped entitlements call).
The `*_flow.json` / `request_logs.json` / `offer.curl` files are derived views
of the same capture.

They are gitignored and local-only. Recapture rather than guess if the SPA
changes: open the journey in Chrome devtools, then Copy all as HAR.
