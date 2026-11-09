# Smoke test, run smoke-021 (2026-11-09)

100 companies from `select_entry_batch.py` (default seed). Real Brønnøysund bulk snapshot. No API key, no paid service.

- 100 of 100 terminal envelopes, validation passed: True (`smoke-report.json`). Run time 114.1 s; 917 requests, p50 444 ms, p95 1354 ms, third-party cost 0.
- 7 of 100 companies have a published official website; 2 more sites are held as `ambiguous` because they failed the identity gate.
- How each published site was found: legal_name_guess 4, registry_filed_website 3.
- Identity basis: registered address on the site 3, organisation number on the site 2, registered phone on the site 1, name evidence only 1.
- 45 dated activity items on 3 companies and 0 job postings, all from company-owned pages, feeds or sitemaps; a date from a sitemap is labelled as a lastmod value and may be a later edit.
- Three separate 300-company samples (seeds 106, 206 and 306): every published site was read, the first two samples by title and excerpt, all 46 sites of the third by independent AI review agents. They led to the rules on holding companies, on redirects to a sister company and on redirects to a page that never names the company. Re-running all 126 companies ever published, before the last activity filter and on the same identity rules, keeps 119: six are the intended drops of those rules and one was a transient fetch miss that was accepted when re-run alone. Figures from those runs are our own and not part of this report.

Most companies in this universe have no web presence, so a low website count is expected. These figures are our own measurements, not Builderr scores. The published sites were reviewed one by one by the operating AI agent, not by a person.
