# Nightshift Signalpost agent

**Built by an AI agent.** Claude (Anthropic), operating Nightshift Labs for one human owner, chose the approach and wrote the code added here. The owner is the entrant and would receive any prize. This is a fork of Builderr's Signalpost starter kit (MIT, see LICENSE and `Builderr starter` below).

## Run it

```bash
pip install -r requirements.txt
python scripts/run_competition_batch.py \
  --organisations companies.jsonl --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl --output out/envelopes.jsonl --report out/report.json \
  --run-id RUN_ID --expected-count N
```

No API key, no model, no paid service: `third_party_cost_usd` is 0. Add `--previous out/old-envelopes.jsonl` to get a refresh diff. `--max-seconds` (or `SIGNALPOST_MAX_SECONDS`, default 5400) is a hard run budget: companies not finished in time get an explicit `failed` result, so a batch always returns one result per input.

## What this adds to the starter

- **Site discovery without a search API** (`discover_site.py`). When the registry lists no working site, candidates come from the registered email domain, then from domains derived from the legal name. A candidate is published only with proof: the exact organisation number on the site; or, for the registered email domain, all legal-name tokens; or, for a name-derived domain, the registered address together with all legal-name tokens.
- **Identity gates** (`identity.py`). Parked and generic pages are rejected. A registry-filed domain carrying the entity's first distinctive name token counts as exact only when the entity is not part of a registered group. Social profiles are published only when the verified site links them and the handle matches.
- **Contract claims** (`claims.py`). One explicit state per information type (`available`, `not_available`, `blocked`, `not_applicable`, `ambiguous`, `failed`). Absence is never turned into zero, and an unverified site produces `ambiguous` rather than facts.
- **Evidence from company pages** (`website.py`). Up to 8 priority pages, schema.org JobPosting, page-stated dates, with hash and source for each.
- **Refresh as a diff** (`changes.py`). Stable claim keys, prior evidence carried forward, idempotent, and a failed refresh is never reported as a removal.

## Browse the results

`python scripts/build_site.py --envelopes out/envelopes.jsonl --output site/index.html` builds one static page, for phone and desktop: find a company, compare two side by side, and open the source of any fact.

## Limits, stated plainly

Prefers missing a fact over publishing one for the wrong company, so coverage of small companies with no web presence is low. It does not access LinkedIn, Meta, Glassdoor, Indeed or Google; job and activity coverage comes only from the company's own site. robots.txt is respected.

---

# Builderr starter (original README)

# Signalpost reference agent

This is a runnable starting point for the Signalpost company-research challenge. It is intentionally a solid baseline, not a winning submission.

The public universe contains 411,160 eligible companies. Run the starter on 100 companies before submitting. Larger local tests, including 1,000 or more companies, are encouraged but their precomputed profiles are not submitted or scored.

## What it already does

- reads a batch of Norwegian organisation numbers;
- anchors identity in the Brønnøysund bulk registry;
- fetches official financials, roles, group links and registered workplaces;
- visits the registry-listed website and rejects weak entity matches;
- emits one terminal JSONL envelope per input;
- records sources, retrieval times, content hashes, request counts and latency;
- supports checkpoint/resume and a deterministic refresh replay;
- includes examples for external-footprint discovery and an evidence-bounded research agent.

## First run: try one saved example

Requires Python 3.12+. Open a terminal inside this extracted folder.

Before downloading company data or running a full crawl, try the bundled public
sample. It uses saved responses: no API key, registry download or live web requests.

```bash
python3 scripts/run_refresh_replay.py \
  --manifest tests/fixtures/refresh-snapshots.json \
  --output out/refresh-demo.json
```

Open `out/refresh-demo.json`. The `events` list shows what changed between two
versions of one company profile and the source evidence for each change. The sample
should find two expected changes, no false changes, and no extra changes when the
same data is checked again.

The report's `qualification_passed` field refers only to this public sample check.
It does not qualify an entry for the competition or prove live information coverage.
The printed request counts are reads from saved responses, not network calls.

## Next: research live companies

Requires Python 3.12+ and `uv`. This step downloads data and makes live requests.
The manifest selector can create a local test batch of any size. Use 100 rows for the recommended smoke test before trying a larger batch.

```bash
uv sync
curl -L 'https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv' -o brreg-enheter.csv
curl -L 'https://builderr.ai/signalpost-company-universe-2025.jsonl.gz' -o signalpost-universe.jsonl.gz

uv run python select_entry_batch.py \
  --universe signalpost-universe.jsonl.gz \
  --count 100 \
  --output entry-companies.jsonl

# Use the 100-company batch as your smoke test.
cp entry-companies.jsonl smoke-companies.jsonl

uv run python scripts/run_competition_batch.py \
  --organisations smoke-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/smoke-profiles.jsonl \
  --output out/smoke-envelopes.jsonl \
  --report out/smoke-report.json \
  --run-id smoke-001 \
  --expected-count 100

# You may test at larger scale locally, but Builderr supplies the official batch for scoring.
uv run python scripts/run_competition_batch.py \
  --organisations entry-companies.jsonl \
  --bulk brreg-enheter.csv \
  --profiles-output out/profiles.jsonl \
  --output out/envelopes.jsonl \
  --report out/run-report.json \
  --run-id local-001 \
  --expected-count 1000

uv run --with pytest pytest -q
```

The published archive was clean-room verified on August 24, 2026: 104 tests and 5 subtests passed, followed by a one-company live BRREG smoke run with one terminal envelope, five requests and zero silent drops.

Increase `--count` and `--expected-count` together for a larger local test. The 100-row smoke test above is practice only; Builderr supplies the companies for every official run.

## The improvement loop

1. Treat the organisation number as the anchor.
2. Generate site/profile candidates from official data, the company site, lawful search providers and named people.
3. Save every candidate and the evidence for or against it.
4. Publish only exact-entity matches. Parent, brand, franchise and similarly named companies are not exact.
5. Crawl static HTML first. Escalate to a browser only when a deterministic completeness check fails.
6. Measure added supported coverage, wrong-company claims, runtime, requests and cost.
7. Promote a strategy only when it improves coverage without weakening the accuracy gates.
8. Freeze strategies and thresholds before the daily evaluation run.

The strongest differentiator is external evidence that remains exact and auditable: official company pages, company-owned profiles, jobs, dated activity, ratings/reviews and permitted public signals. Do not trade accuracy for volume.

## Important source rule

Open-source code does not grant permission to scrape a platform. Follow each source's terms, rate limits and licence. LinkedIn, Meta and Indeed are useful identity/discovery targets, but direct automated collection may be restricted. Use permitted APIs, licensed providers, company-owned outbound links, or return `blocked`/`not_available`.

Read `docs/competition-control-loop.md`, `docs/external-connectors.md` and the public source policy before adding connectors.

## Submission contract

Submit a repository with:

- a 100-company smoke-test result or report;
- one documented command that accepts a JSONL batch of organisation numbers;
- exactly one terminal envelope per input;
- pinned dependencies and reproducible setup;
- a previous-snapshot input and material-change output;
- a machine-readable run report with runtime, request count and third-party cost;
- declared models, APIs, licences and source-rights assumptions.

Email the repository URL, run command, models/APIs and expected cost per 100-company run to `submit@builderr.ai`.
