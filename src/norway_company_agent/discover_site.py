"""Credential-free official-site discovery for companies whose registry website is missing or fails.

Candidates come only from registry data: the domain of the registered email address, then domains
derived from the legal name. A candidate is a guess until the fetched site proves it: publication
requires the exact organisation number on the site (any candidate) or, for the registered-email
domain only, all legal-name tokens in the homepage identity text. Search rank is never evidence.
"""
from __future__ import annotations

import re
import socket
import unicodedata
from typing import Any, Callable

from .identity import LEGAL_AND_GENERIC, apply_website_identity_gate
from .website import fetch_website

WEBMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no",
    "yahoo.com", "yahoo.no", "icloud.com", "me.com", "mac.com", "msn.com", "aol.com", "proton.me", "protonmail.com",
    "online.no", "broadpark.no", "getmail.no", "altibox.no", "lyse.net", "start.no", "frisurf.no", "c2i.net",
    "chello.no", "telenor.no", "tele2.no", "nextgentel.no", "bluecom.no", "ebnett.no", "enivest.no", "haugnett.no",
    "gmx.com", "gmx.net", "mail.com", "tussa.com", "sensewave.no", "vikenfiber.no", "tdc.no", "loqal.no", "skynet.no",
    "regnskapnorge.no", "accountor.no",
}
MAX_NAME_CANDIDATES = 4


def _ascii_tokens(name: str) -> list[str]:
    text = name.translate(str.maketrans({"ø": "o", "Ø": "O", "å": "a", "Å": "A", "æ": "ae", "Æ": "AE"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
    return [token for token in re.findall(r"[a-z0-9]+", text) if token not in LEGAL_AND_GENERIC]


def email_domain(email: Any) -> str | None:
    match = re.search(r"@([a-z0-9.-]+\.[a-z]{2,})\s*$", str(email or "").strip().casefold())
    if not match:
        return None
    domain = match.group(1).removeprefix("www.")
    return None if domain in WEBMAIL else domain


def name_candidates(name: str) -> list[str]:
    tokens = _ascii_tokens(name)
    if not tokens or (len(tokens) == 1 and len(tokens[0]) < 5):
        return []  # a short single token guesses someone else's domain far too easily
    joined = "".join(tokens)
    candidates = []
    if len(joined) <= 40:
        candidates += [f"{joined}.no", f"{joined}.com"]
    if len(tokens) >= 2:
        candidates += ["-".join(tokens) + ".no", "".join(tokens[:2]) + ".no"]
    return list(dict.fromkeys(candidates))[:MAX_NAME_CANDIDATES]


def resolves(domain: str) -> bool:
    try:
        socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
        return True
    except OSError:
        return False


def _org_number_proven(website: dict[str, Any], org: str) -> bool:
    value = website.get("value") or {}
    return bool(value.get("org_number_found_at")) and org == re.sub(r"\D", "", str(value.get("org_number_found") or ""))


def discover(
    profile: dict[str, Any],
    registered_email: Any,
    fetch: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = fetch_website,
    dns: Callable[[str], bool] = resolves,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (website evidence, metrics); evidence is None unless a candidate was proven exact."""
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    metrics: dict[str, Any] = {"requests": 0, "bytes": 0, "latencies_ms": [], "candidates": []}
    domain = email_domain(registered_email)
    ordered = ([("registered_email_domain", domain)] if domain else []) + [("legal_name_guess", item) for item in name_candidates(str(profile.get("name") or ""))]
    tried: set[str] = set()
    for origin, candidate in ordered:
        if candidate in tried:
            continue
        tried.add(candidate)
        if not dns(candidate):
            metrics["candidates"].append({"domain": candidate, "origin": origin, "outcome": "no_dns"})
            continue
        record, fetched = fetch(candidate)
        metrics["requests"] += fetched["requests"]
        metrics["bytes"] += fetched["bytes"]
        metrics["latencies_ms"] += fetched["latencies_ms"]
        if record.get("status") != "available":
            metrics["candidates"].append({"domain": candidate, "origin": origin, "outcome": record.get("status")})
            continue
        gated = apply_website_identity_gate(profile, record)["website"]
        assessment = (gated.get("value") or {}).get("identity_assessment") or {}
        proven = _org_number_proven(gated, org)
        accepted = proven or (origin == "registered_email_domain" and assessment.get("publishable"))
        metrics["candidates"].append({"domain": candidate, "origin": origin, "outcome": "accepted" if accepted else "rejected_identity", "org_number_on_site": proven})
        if accepted:
            value = gated["value"]
            value["discovery"] = {"origin": origin, "candidate": candidate, "org_number_on_site": proven, "method": "registry_derived_candidate_v1"}
            value["identity_assessment"] = {**assessment, "publishable": True, "status": "exact"}
            return gated, metrics
    return None, metrics
