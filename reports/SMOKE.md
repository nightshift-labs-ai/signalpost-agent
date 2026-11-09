# Smoke test, run smoke-005 (2026-11-09)

100 companies from `select_entry_batch.py` (default seed). Real Brønnøysund bulk snapshot. No API key, no paid service.

- 100 of 100 terminal envelopes, zero silent drops (`smoke-report.json`), 2 min 21 s on 4 CPU cores.
- 912 requests, p50 508 ms, p95 1,123 ms, third-party cost 0.
- 8 of 100 companies have a published official website; 2 more sites are held as `ambiguous` because they failed the identity gate.
- 3 of the 8 were not in the registry at all and were found through registry-derived candidates; all proofs were hand-read.
- Re-run against the previous run: 0 changes on 99 companies, 1 correct removal (a parked domain).

Most companies in this universe have no web presence, so a low website count is expected. These figures are our own measurements, not Builderr scores.
