"""Refresh as a diff: compare this run's claims with a previous envelope of the same company.

The previous envelope's evidence is carried forward (`prior_evidence`), so an old value never loses
its source. A claim in `failed` or `blocked` state is not a change: a failed refresh must not read as
the fact having disappeared.
"""
from __future__ import annotations

import json
from typing import Any

UNCERTAIN = {"failed", "blocked", "ambiguous"}
WEBSITE_FAMILIES = {"official_website", "company_profile", "self_description", "hiring", "public_activity"}


def _family(field: str) -> str:
    return field.split(".")[0]


def _uncertain_families(claims: list[dict[str, Any]]) -> set[str]:
    """Families whose absence this run says nothing about: a failed module, or everything read from a site that could not be verified."""
    families = {_family(claim["field"]) for claim in claims if claim.get("availability") in UNCERTAIN}
    return families | WEBSITE_FAMILIES if "official_website" in families else families


def _stable(value: Any) -> Any:
    """Drop empty keys so a schema addition with no data is not reported as a change."""
    if isinstance(value, dict):
        return {key: _stable(item) for key, item in value.items() if item not in (None, "", [], {})}
    return value


def _key(claim: dict[str, Any]) -> str:
    value = claim.get("value")
    if isinstance(value, dict):
        if claim["field"] == "registered_workplace" and value.get("organisation_number"):
            return claim["field"] + "|" + str(value["organisation_number"])
        if claim["field"] == "leadership.role":
            return claim["field"] + "|" + "|".join(str(value.get(part)) for part in ("name", "role_code"))
        if claim["field"] in {"hiring.job_posting", "public_activity.dated_item"}:
            return claim["field"] + "|" + str(value.get("posting_url") or value.get("url") or value.get("title"))
        if claim["field"] == "business_address":
            return claim["field"]
        return claim["field"] + "|" + json.dumps(_stable(value), ensure_ascii=False, sort_keys=True)
    period = claim.get("reporting_period") or {}
    return claim["field"] + ("|" + json.dumps(period, sort_keys=True) + "|" + str(claim.get("account_type") or "") if period else "")


def _index(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_key(claim): claim for claim in claims if claim.get("availability") == "available"}


def diff(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    new, old = _index(current), _index(previous)
    uncertain_now = _uncertain_families(current)
    uncertain_before = _uncertain_families(previous)
    oldest_kept: dict[str, str] = {}
    for claim in new.values():
        end = (claim.get("reporting_period") or {}).get("tilDato")
        if end:
            oldest_kept[claim["field"]] = min(end, oldest_kept.get(claim["field"], end))
    changes: list[dict[str, Any]] = []
    for key in sorted(set(new) | set(old)):
        before, after = old.get(key), new.get(key)
        if before and after:
            if _stable(before.get("value")) != _stable(after.get("value")):
                changes.append({"type": "changed", "field": after["field"], "previous": before["value"], "current": after["value"], "evidence_ids": after.get("evidence_ids"), "previous_evidence_ids": before.get("evidence_ids")})
        elif after:
            if _family(after["field"]) in uncertain_before:
                continue  # the source was unavailable last time: seeing it now is not news about the company
            changes.append({"type": "added", "field": after["field"], "current": after["value"], "evidence_ids": after.get("evidence_ids")})
        elif before and _family(before["field"]) not in uncertain_now:
            end = (before.get("reporting_period") or {}).get("tilDato")
            if end and before["field"] in oldest_kept and end < oldest_kept[before["field"]]:
                continue  # an older year rolled out of the kept window when a newer filing arrived
            changes.append({"type": "removed", "field": before["field"], "previous": before["value"], "previous_evidence_ids": before.get("evidence_ids")})
    return changes


def _short(value: Any) -> str:
    """A change's value as a few words: the name or title of a record, or the value itself."""
    if isinstance(value, dict):
        value = next((value[key] for key in ("title", "name", "beskrivelse", "posting_url", "url") if value.get(key)), None) or ", ".join(str(item) for item in value.values() if isinstance(item, (str, int, float)))
    text = " ".join(str(value).split())
    return text if len(text) <= 80 else text[:80].rsplit(" ", 1)[0] + "..."


def _change_text(change: dict[str, Any]) -> str:
    label = change["field"].replace(".", " ").replace("_", " ")
    if change["type"] == "added":
        return f"new {label}: {_short(change.get('current'))}"
    if change["type"] == "removed":
        return f"{label} no longer found (was {_short(change.get('previous'))})"
    return f"{label} changed from {_short(change.get('previous'))} to {_short(change.get('current'))}"


def _refresh_sentence(envelope: dict[str, Any], previous: dict[str, Any] | None) -> None:
    """State in the summary what this run can say about change, including that it cannot say anything yet."""
    summary = envelope.get("summary")
    if not isinstance(summary, dict) or not isinstance(summary.get("sentences"), list):
        return
    evidence_ids: list[str] = []
    if not previous:
        text = "This is the first run for this company, so there is no earlier run to compare it with."
    else:
        changes = envelope.get("changes") or []
        if changes:
            shown = changes[:3]
            more = f", and {len(changes) - len(shown)} more" if len(changes) > len(shown) else ""
            text = "Since the previous run: " + "; ".join(_change_text(change) for change in shown) + more + "."
            run_id = str(previous.get("run_id"))
            for change in shown:
                evidence_ids += change.get("evidence_ids") or [f"{evidence_id}@{run_id}" for evidence_id in change.get("previous_evidence_ids") or []]
        else:
            text = "No material change was found since the previous run."
        if _uncertain_families(envelope.get("claims") or []):
            text += " Sources that could not be read in this run are not counted as changes."
    sentences = [item for item in summary["sentences"] if item.get("kind") != "refresh"]
    sentences.append({"text": text, "evidence_ids": sorted(set(evidence_ids)), "kind": "refresh"})
    summary["sentences"] = sentences
    summary["text"] = " ".join(item["text"] for item in sentences)


def apply_previous(envelope: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Fill `changes` and carry prior evidence forward. Idempotent for an identical snapshot."""
    if not previous:
        envelope["refresh"] = {"previous_run_id": None, "first_observation": True}
        _refresh_sentence(envelope, None)
        return envelope
    envelope["changes"] = diff(envelope["claims"], previous.get("claims") or [])
    carried = {row["id"]: row for row in previous.get("prior_evidence") or []}
    carried.update({row["id"] + "@" + str(previous.get("run_id")): {**row, "id": row["id"] + "@" + str(previous.get("run_id"))} for row in previous.get("evidence") or []})
    envelope["prior_evidence"] = list(carried.values())
    envelope["refresh"] = {"previous_run_id": previous.get("run_id"), "first_observation": False, "material_changes": len(envelope["changes"])}
    _refresh_sentence(envelope, previous)
    return envelope
