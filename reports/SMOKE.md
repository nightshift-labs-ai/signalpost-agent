# Smoke test, run smoke-017 (2026-11-09)

100 companies from `select_entry_batch.py` (default seed). Real Brønnøysund bulk snapshot. No API key, no paid service.

- 100 of 100 terminal envelopes, validation passed: True (`smoke-report.json`). Run time 114.0 s; 917 requests, p50 452 ms, p95 1181 ms, third-party cost 0.
- 8 of 100 companies have a published official website; 1 more sites are held as `ambiguous` because they failed the identity gate.
- How each published site was found: legal_name_guess 5, registry_filed_website 3.
- Identity basis: registered address on the site 4, organisation number on the site 2, registered phone on the site 1, name evidence only 1.
- 57 dated activity items on 4 companies and 0 job postings, all from company-owned pages, feeds or sitemaps; a date from a sitemap is labelled as a lastmod value and may be a later edit.
- A separate 300-company sample (seed 106) on the code just before the last rule: 20 sites published in 257 s, each read one by one; one (a holding company on its operating company's site) led to the rule that a holding company needs the exact organisation number. Figures from that run are our own and not part of this report.

Most companies in this universe have no web presence, so a low website count is expected. These figures are our own measurements, not Builderr scores. The published sites were reviewed one by one by the operating AI agent, not by a person.
