# Smoke test, run smoke-006 (2026-11-09)

100 companies from `select_entry_batch.py` (default seed). Real Brønnøysund bulk snapshot. No API key, no paid service.

- 100 of 100 terminal envelopes, zero silent drops (`smoke-report.json`), 2 min 10 s on 4 CPU cores.
- 912 requests, p50 511 ms, p95 1,150 ms, third-party cost 0.
- 8 of 100 companies have a published official website; 2 more sites are held as `ambiguous` because they failed the identity gate.
- 5 of the 8 had no working website in the registry and were found through registry-derived candidates; the URL and page title of all 8 were read by hand against the company name, and the 2 with the organisation number on the site were confirmed exact.
- Refresh check (previous run of this batch): 0 changes on 99 companies, 1 correct removal (a parked domain caught by the identity gate).
- A second 100-company sample was read by hand: 8 sites published, and the one parent-company site it exposed is now rejected by code and a test.

Most companies in this universe have no web presence, so a low website count is expected. These figures are our own measurements, not Builderr scores.
