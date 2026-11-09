from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
import urllib.parse

from .evidence import evidence, utc_now
from .official import accounting_obligation_assessment
from .claims import build_claims
from .sampling import iter_bulk


TERMINAL_STATES = {
    "complete",
    "not_applicable",
    "not_found",
    "blocked_policy",
    "blocked_robots",
    "source_error",
    "budget_exhausted",
    "submission_error",
}


def read_organisation_inputs(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    values: list[Any]
    if source.suffix == ".json":
        body = json.loads(text)
        values = body if isinstance(body, list) else body.get("organisation_numbers", [])
    elif source.suffix == ".jsonl":
        values = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        values = [line.strip() for line in text.splitlines() if line.strip()]
    records = []
    for value in values:
        org = next((value[key] for key in ("organisation_number", "organisasjonsnummer", "orgnr", "organizationNumber") if value.get(key)), None) if isinstance(value, dict) else value
        org = "".join(character for character in str(org or "") if character.isdigit())
        if len(org) != 9:
            raise ValueError(f"Invalid Norwegian organisation number: {value!r}")
        record = {"organisation_number": org}
        if isinstance(value, dict):
            for key in ("evaluation_split", "sample_slice"):
                if value.get(key) is not None:
                    record[key] = value[key]
        records.append(record)
    orgs = [record["organisation_number"] for record in records]
    if len(orgs) != len(set(orgs)):
        raise ValueError("Organisation-number input contains duplicates")
    return records


def read_organisation_numbers(path: str | Path) -> list[str]:
    return [record["organisation_number"] for record in read_organisation_inputs(path)]


def _filed_domain(website: Any) -> str | None:
    text = str(website or "").strip().casefold()
    if not text:
        return None
    host = urllib.parse.urlparse(text if "://" in text else "https://" + text).hostname or ""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else None


def profiles_from_bulk(path: str | Path, organisation_numbers: Iterable[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    requested = list(organisation_numbers)
    wanted = set(requested)
    snapshot_sha256 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    retrieved_at = utc_now()
    found: dict[str, dict[str, Any]] = {}
    scanned = 0
    domain_filers: Counter[str] = Counter()
    for profile in iter_bulk(path):
        scanned += 1
        org = profile["organisation_number"]
        domain = _filed_domain(profile.get("website"))
        if domain:
            domain_filers[domain] += 1
        if org not in wanted:
            continue
        raw = profile.pop("raw", {})
        profile["evidence"] = {
            "registry": evidence(
                "registry",
                "available",
                "official_registry_bulk",
                "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv",
                value=raw,
                retrieved_at=retrieved_at,
                content_sha256=snapshot_sha256,
                source_row_key=org,
            ),
            "accounting_obligation": accounting_obligation_assessment(profile),
        }
        found[org] = profile
    for profile in found.values():
        domain = _filed_domain(profile.get("website"))
        profile["registered_domain_filers"] = domain_filers[domain] if domain else 0
    missing = [org for org in requested if org not in found]
    for org in missing:  # still gets a terminal result; registry_live is fetched for it later like for any other company
        found[org] = {
            "organisation_number": org,
            "name": None,
            "website": None,
            "registered_domain_filers": 0,
            "evidence": {
                "registry": evidence("registry", "not_found", "official_registry_bulk", "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", note="Not present in the supplied registry snapshot", retrieved_at=retrieved_at, content_sha256=snapshot_sha256),
                "accounting_obligation": evidence("accounting_obligation", "not_found", "official_registry_bulk", "https://data.brreg.no/enhetsregisteret/api/enheter/lastned/csv", note="No registry row to classify", retrieved_at=retrieved_at),
            },
        }
    return [found[org] for org in requested], {
        "registry_snapshot_sha256": snapshot_sha256,
        "registry_rows_scanned": scanned,
        "requested": len(requested),
        "selected": len(found) - len(missing),
        "missing_from_snapshot": missing,
    }


def evidence_terminal_state(record: dict[str, Any] | None) -> str:
    if not record:
        return "submission_error"
    status = record.get("status")
    if status == "available":
        return "complete"
    if status == "not_applicable":
        return "not_applicable"
    if status == "not_found":
        return "not_found"
    if status == "blocked":
        note = str(record.get("note") or "").casefold()
        return "blocked_robots" if "robot" in note else "blocked_policy"
    if status == "source_error":
        return "source_error"
    return "submission_error"


def terminal_envelope(
    profile: dict[str, Any],
    *,
    run_id: str,
    modules: Iterable[str],
    started_at: str,
    completed_at: str,
) -> dict[str, Any]:
    module_states = {}
    for module in modules:
        record = profile.get("evidence", {}).get(module)
        module_states[module] = {
            "state": evidence_terminal_state(record),
            "retry_count": int((record or {}).get("retry_count") or 0),
            "final_timestamp": (record or {}).get("retrieved_at") or completed_at,
        }
    entity_state = "submission_error" if any(item["state"] == "submission_error" for item in module_states.values()) else "complete"
    claims, claim_evidence, summary = build_claims(profile)
    return {
        "run_id": run_id,
        "run": {"run_id": run_id, "started_at": started_at, "completed_at": completed_at, "terminal_status": "completed" if entity_state == "complete" else entity_state},
        "organisation_number": profile["organisation_number"],
        "state": entity_state,
        "started_at": started_at,
        "completed_at": completed_at,
        "modules": module_states,
        "claims": claims,
        "evidence": claim_evidence,
        "summary": summary,
        "changes": [],
        "errors": [{"module": module, "state": item["state"]} for module, item in module_states.items() if item["state"] in {"source_error", "submission_error"}],
        "operations": {"third_party_cost_usd": 0, **{key: value for key, value in (profile.get("run_metrics") or {}).items() if key != "latencies_ms"}},
        "profile": _envelope_profile(profile),
    }


def _envelope_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """A site that failed the identity gate stays out of the published envelope; --profiles-output keeps the full record."""
    website = (profile.get("evidence") or {}).get("website") or {}
    assessment = (website.get("value") or {}).get("identity_assessment") or {}
    view = {key: value for key, value in profile.items() if key != "site_discovery"}
    if website.get("status") == "available" and not assessment.get("publishable"):
        view["evidence"] = {**profile["evidence"], "website": {"status": "ambiguous", "note": "A candidate site was found but did not pass the identity gate, so it is not published.", "retrieved_at": website.get("retrieved_at")}}
    return view


def validate_envelopes(envelopes: list[dict[str, Any]], expected_count: int) -> dict[str, Any]:
    orgs = [item.get("organisation_number") for item in envelopes]
    invalid_states = [
        {"organisation_number": item.get("organisation_number"), "state": state.get("state")}
        for item in envelopes
        for state in item.get("modules", {}).values()
        if state.get("state") not in TERMINAL_STATES
    ]
    checks = {
        "exact_expected_count": len(envelopes) == expected_count,
        "unique_organisation_numbers": len(orgs) == len(set(orgs)),
        "all_entity_states_terminal": all(item.get("state") in TERMINAL_STATES for item in envelopes),
        "all_module_states_terminal": not invalid_states,
        "zero_silent_drops": len(envelopes) == expected_count and len(orgs) == len(set(orgs)),
    }
    return {"passed": all(checks.values()), "checks": checks, "invalid_states": invalid_states}


def profile_complete_for_modules(profile: dict[str, Any], modules: Iterable[str]) -> bool:
    records = profile.get("evidence", {})
    return all(module in records and records[module].get("status") != "not_fetched" for module in modules)
