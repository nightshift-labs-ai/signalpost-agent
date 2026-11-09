#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import json
import signal
import sys
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from norway_company_agent.batch import profile_complete_for_modules, profiles_from_bulk, read_organisation_inputs, terminal_envelope, validate_envelopes  # noqa: E402
from norway_company_agent.evidence import evidence, utc_now  # noqa: E402
from norway_company_agent.identity import _tokens, apply_website_identity_gate  # noqa: E402
from norway_company_agent.official import fetch_official_modules  # noqa: E402
from norway_company_agent.website import fetch_website  # noqa: E402
from norway_company_agent.discover_site import discover  # noqa: E402
from norway_company_agent.changes import apply_previous  # noqa: E402


FINALIZE_RESERVE_SECONDS = 30.0


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", errors="replace") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    temporary.replace(path)


def mark_unfinished(profile: dict, modules: list[str], reason: str) -> None:
    """Give every module a terminal `source_error` record, so an unfinished company is reported, not dropped."""
    records = profile.setdefault("evidence", {})
    for module in modules:
        record = records.get(module)
        if not record or record.get("status") == "not_fetched":
            records[module] = evidence(module, "source_error", "run_budget", "urn:signalpost:run-budget", note=reason)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluator-owned Signalpost batch contract")
    parser.add_argument("--organisations", required=True, help="JSON, JSONL, or text organisation-number list")
    parser.add_argument("--bulk", required=True, help="Frozen Brreg entity snapshot")
    parser.add_argument("--output", required=True, help="Terminal envelope JSONL")
    parser.add_argument("--profiles-output", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-count", type=int, default=100)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument("--max-seconds", type=float, default=float(os.environ.get("SIGNALPOST_MAX_SECONDS", "5400")), help="Hard run budget. Companies not finished by then get an explicit failed result instead of being dropped.")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--previous", help="Envelope JSONL from an earlier run; enables the refresh diff")
    parser.add_argument("--modules", default="registry,accounting_obligation,registry_live,financials,roles,group,locations,website")
    args = parser.parse_args()

    started_at = utc_now()
    organisation_inputs = read_organisation_inputs(args.organisations)
    orgs = [item["organisation_number"] for item in organisation_inputs]
    if len(orgs) != args.expected_count:
        raise SystemExit(f"Expected {args.expected_count} organisations, received {len(orgs)}")
    profiles, registry_metadata = profiles_from_bulk(args.bulk, orgs)
    annotations = {item["organisation_number"]: item for item in organisation_inputs}
    for profile in profiles:
        for key in ("evaluation_split", "sample_slice"):
            if key in annotations[profile["organisation_number"]]:
                profile[key] = annotations[profile["organisation_number"]][key]
    requested_modules = [item.strip() for item in args.modules.split(",") if item.strip()]
    fetch_modules = set(requested_modules) - {"registry", "accounting_obligation", "website"}
    operations = {"requests": 0, "bytes": 0, "latencies_ms": []}

    wikidata_path = Path(__file__).resolve().parents[1] / "data" / "wikidata_org_websites.json"
    wikidata_sites = json.loads(wikidata_path.read_text(encoding="utf-8")) if wikidata_path.exists() else {}

    def enrich(profile: dict) -> tuple[dict, dict]:
        started_company = time.monotonic()
        records, metrics = fetch_official_modules(profile["organisation_number"], fetch_modules)
        profile["evidence"].update(records)
        website_metrics = {"requests": 0, "bytes": 0, "latencies_ms": []}
        if "website" in requested_modules:
            org = profile["organisation_number"]
            live = (profile["evidence"].get("registry_live") or {}).get("value") or {}
            address = live.get("business_address") or {}
            street_lines = address.get("adresse") or []
            street = next((word for line in street_lines for word in str(line).split() if len(word) >= 4 and not word[0].isdigit()), "")
            if not profile.get("name"):
                profile.update({"name": live.get("name"), "legal_form": live.get("legal_form"), "website": live.get("website")})
            hints = {"postcode": address.get("postnummer"), "street": street, "phones": live.get("phones") or [], "name_tokens": [token for token in _tokens(profile.get("name")) if len(token) >= 4]}
            website_record, website_metrics = fetch_website(profile.get("website"), org=org, hints=hints)
            gated = apply_website_identity_gate(profile, website_record)["website"]
            profile["evidence"]["website"] = gated
            publishable = ((gated.get("value") or {}).get("identity_assessment") or {}).get("publishable")
            if not publishable:
                found, discovery_metrics = discover(profile, live.get("email"), subunit_sites=[item.get("website") for item in ((profile["evidence"].get("locations") or {}).get("value") or {}).get("locations", []) if item.get("website")], extra_sites=wikidata_sites.get(org), fetch=lambda url: fetch_website(url, org=org, hints=hints))
                website_metrics["requests"] += discovery_metrics["requests"]
                website_metrics["bytes"] += discovery_metrics["bytes"]
                website_metrics["latencies_ms"] += discovery_metrics["latencies_ms"]
                profile["site_discovery"] = {"registry_site_publishable": bool(publishable), "candidates": discovery_metrics["candidates"]}
                if found:
                    profile["evidence"]["website"] = found
        metric = {
            "requests": len(metrics) + website_metrics["requests"],
            "bytes": sum(item.bytes_received for item in metrics) + website_metrics["bytes"],
            "latencies_ms": [item.elapsed_ms for item in metrics] + website_metrics["latencies_ms"],
            "runtime_ms": int((time.monotonic() - started_company) * 1000),
        }
        profile["run_metrics"] = metric
        return profile, metric

    state: dict[str, dict] = {}
    resumed_profiles = 0
    profiles_output = Path(args.profiles_output)
    if args.resume and profiles_output.exists():
        prior = [json.loads(line) for line in profiles_output.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not set(item["organisation_number"] for item in prior).issubset(set(orgs)):
            raise SystemExit("Resume profile membership is not a subset of this batch")
        state = {
            item["organisation_number"]: item
            for item in prior
            if profile_complete_for_modules(item, requested_modules)
        }
        resumed_profiles = len(state)
    pending_profiles = [profile for profile in profiles if profile["organisation_number"] not in state]
    run_started = time.monotonic()
    deadline = run_started + max(1.0, args.max_seconds - FINALIZE_RESERVE_SECONDS)
    pristine = {profile["organisation_number"]: copy.deepcopy(profile) for profile in pending_profiles}
    previous = {}
    if args.previous and Path(args.previous).exists():
        previous = {row["organisation_number"]: row for row in (json.loads(line) for line in Path(args.previous).read_text(encoding="utf-8").splitlines() if line.strip())}

    def build_envelope(profile: dict, completed_at: str) -> dict:
        """One company must never cost the whole run its output."""
        try:
            return apply_previous(terminal_envelope(profile, run_id=args.run_id, modules=requested_modules, started_at=started_at, completed_at=completed_at), previous.get(profile["organisation_number"]))
        except Exception as exc:
            org = profile["organisation_number"]
            modules = {module: {"state": "submission_error", "retry_count": 0, "final_timestamp": completed_at} for module in requested_modules}
            return {"run_id": args.run_id, "run": {"run_id": args.run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": "submission_error"}, "organisation_number": org, "state": "submission_error", "started_at": started_at, "completed_at": completed_at, "modules": modules, "claims": [], "evidence": [], "summary": {"text": "", "sentences": []}, "changes": [], "errors": [{"module": "envelope", "state": "submission_error", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}], "operations": {"third_party_cost_usd": 0}, "profile": {"organisation_number": org}}

    def finalize(reason: str) -> tuple[dict, int]:
        """Write envelopes and report for everything known so far; unfinished companies get an explicit failed record."""
        completed_at = utc_now()
        done = dict(state)
        ordered, unfinished = [], []
        for org in orgs:
            profile = done.get(org)
            if profile is None:
                profile = copy.deepcopy(pristine[org])
                mark_unfinished(profile, requested_modules, reason)
                unfinished.append(org)
            ordered.append(profile)
        envelopes = [build_envelope(profile, completed_at) for profile in ordered]
        validation = validate_envelopes(envelopes, args.expected_count)
        write_jsonl(profiles_output, ordered)
        write_jsonl(Path(args.output), envelopes)
        latencies = sorted(operations["latencies_ms"])
        report = {
            "run_id": args.run_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "expected_count": args.expected_count,
            "emitted_envelopes": len(envelopes),
            "resumed_profiles": resumed_profiles,
            "unfinished_at_time_budget": len(unfinished),
            "profiles_fetched_this_run": len(pending_profiles),
            "modules": requested_modules,
            "registry": registry_metadata,
            "operations": {"requests": operations["requests"], "bytes": operations["bytes"], "p50_ms": latencies[len(latencies) // 2] if latencies else None, "p95_ms": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else None},
            "third_party_cost_usd": 0,
            "runtime_seconds": round(time.monotonic() - run_started, 1),
            "validation": validation,
        }
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report, 0 if validation["passed"] else 1

    def on_terminate(_signum: int, _frame: object) -> None:
        finalize("the run was stopped before this company finished")
        sys.stdout.flush()
        os._exit(1)

    signal.signal(signal.SIGTERM, on_terminate)
    pool = ThreadPoolExecutor(max_workers=args.workers)
    futures = {pool.submit(enrich, profile): profile for profile in pending_profiles}
    try:
        for index, future in enumerate(as_completed(futures, timeout=max(0.0, deadline - time.monotonic())), 1):
            try:
                profile, metric = future.result()
            except Exception as exc:  # one bad company must never lose the run
                profile, metric = futures[future], {"requests": 0, "bytes": 0, "latencies_ms": []}
                mark_unfinished(profile, requested_modules, f"{type(exc).__name__}: {str(exc)[:160]}")
            state[profile["organisation_number"]] = profile
            operations["requests"] += metric["requests"]
            operations["bytes"] += metric["bytes"]
            operations["latencies_ms"].extend(metric["latencies_ms"])
            if index % args.checkpoint_every == 0 or index == len(pending_profiles):
                finalize("the run had not reached this company at this checkpoint")
    except FuturesTimeout:
        pool.shutdown(wait=False, cancel_futures=True)

    report, exit_code = finalize("run time budget reached before this company finished")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.stdout.flush()
    # Abandoned worker threads may still sit in a network timeout; they must not hold the process open.
    os._exit(exit_code)


if __name__ == "__main__":
    main()
