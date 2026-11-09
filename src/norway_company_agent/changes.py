"""Refresh as a diff: compare this run's claims with a previous envelope of the same company.

The previous envelope's evidence is carried forward (`prior_evidence`), so an old value never loses
its source. A claim in `failed` or `blocked` state is not a change: a failed refresh must not read as
the fact having disappeared.
"""
from __future__ import annotations

import json
from typing import Any

UNCERTAIN = {"failed", "blocked", "ambiguous"}


def _key(claim: dict[str, Any]) -> str:
    value = claim.get("value")
    if claim["field"] in {"leadership.role", "registered_workplace", "hiring.job_posting", "public_activity.dated_item"} and isinstance(value, dict):
        return claim["field"] + "|" + json.dumps(value, ensure_ascii=False, sort_keys=True)
    period = claim.get("reporting_period") or {}
    return claim["field"] + ("|" + json.dumps(period, sort_keys=True) if period else "")


def _index(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_key(claim): claim for claim in claims if claim.get("availability") == "available"}


def diff(current: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
    new, old = _index(current), _index(previous)
    uncertain_fields = {claim["field"] for claim in current if claim.get("availability") in UNCERTAIN}
    changes: list[dict[str, Any]] = []
    for key in sorted(set(new) | set(old)):
        before, after = old.get(key), new.get(key)
        if before and after:
            if before.get("value") != after.get("value"):
                changes.append({"type": "changed", "field": after["field"], "previous": before["value"], "current": after["value"], "evidence_ids": after.get("evidence_ids"), "previous_evidence_ids": before.get("evidence_ids")})
        elif after:
            changes.append({"type": "added", "field": after["field"], "current": after["value"], "evidence_ids": after.get("evidence_ids")})
        elif before and before["field"] not in uncertain_fields:
            changes.append({"type": "removed", "field": before["field"], "previous": before["value"], "previous_evidence_ids": before.get("evidence_ids")})
    return changes


def apply_previous(envelope: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Fill `changes` and carry prior evidence forward. Idempotent for an identical snapshot."""
    if not previous:
        envelope["refresh"] = {"previous_run_id": None, "first_observation": True}
        return envelope
    envelope["changes"] = diff(envelope["claims"], previous.get("claims") or [])
    carried = {row["id"]: row for row in previous.get("prior_evidence") or []}
    if envelope["changes"]:
        carried.update({row["id"] + "@" + str(previous.get("run_id")): {**row, "id": row["id"] + "@" + str(previous.get("run_id"))} for row in previous.get("evidence") or []})
    envelope["prior_evidence"] = list(carried.values())
    envelope["refresh"] = {"previous_run_id": previous.get("run_id"), "first_observation": False, "material_changes": len(envelope["changes"])}
    return envelope
