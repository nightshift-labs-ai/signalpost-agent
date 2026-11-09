"""Credential-free official-site discovery for companies whose registry website is missing or fails.

Candidates come only from registry data: the domain of the registered email address, then domains
derived from the legal name. A candidate is a guess until the fetched site proves it: publication
requires the exact organisation number on the site; or the registered phone number, or the registered daily manager or board chair named in full, together with all legal-name tokens or a domain equal to the legal name; or, for the registered-email domain, a domain
label equal to the joined legal-name tokens or all legal-name tokens (a matching address is not enough there: the domain may belong to the company's
accountant); or, for a name-derived domain, the registered address together with all legal-name
tokens. A domain outside .no always needs the organisation number: it is often the foreign parent's. Search rank is never evidence.
"""
from __future__ import annotations

import re
import socket
import threading
import time
import unicodedata
import urllib.parse
from typing import Any, Callable

from .identity import LEGAL_AND_GENERIC, _tokens, apply_website_identity_gate
from .website import _fold, _registered_domain, fetch_website

WEBMAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "hotmail.no", "outlook.com", "outlook.no", "live.com", "live.no",
    "yahoo.com", "yahoo.no", "icloud.com", "me.com", "mac.com", "msn.com", "aol.com", "proton.me", "protonmail.com",
    "online.no", "broadpark.no", "getmail.no", "altibox.no", "lyse.net", "start.no", "frisurf.no", "c2i.net",
    "chello.no", "telenor.no", "tele2.no", "nextgentel.no", "bluecom.no", "ebnett.no", "enivest.no", "haugnett.no",
    "gmx.com", "gmx.net", "mail.com", "tussa.com", "sensewave.no", "vikenfiber.no", "tdc.no", "loqal.no", "skynet.no",
    "regnskapnorge.no", "accountor.no",
}
MAX_NAME_CANDIDATES = 14
DISCOVERY_BUDGET_SECONDS = 75.0
TRADE_WORDS = {"holding", "invest", "investering", "eiendom", "eiendommer", "gruppen", "consult", "consulting", "konsult", "drift", "service", "services", "firma", "arkitektfirma", "regnskap", "bygg", "transport", "elektro", "maskin", "entreprenor"}
TRADE_STEMS = {"arkitektfirma": "arkitekt", "eiendommer": "eiendom", "gruppen": "gruppe", "konsult": "consult", "services": "service", "investering": "invest"}


def _ascii_tokens(name: str, *, long_forms: bool = False) -> list[str]:
    """Registered names as domain labels. long_forms spells ø/å as oe/aa, the form many older .no domains kept."""
    table = {"ø": "oe", "Ø": "OE", "å": "aa", "Å": "AA"} if long_forms else {"ø": "o", "Ø": "O", "å": "a", "Å": "A"}
    text = name.translate(str.maketrans({**table, "æ": "ae", "Æ": "AE"}))
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
    core = [token for token in tokens if token not in TRADE_WORDS]
    if core and len("".join(core)) >= 5 and core != tokens:
        candidates += ["".join(core) + ".no", "".join(core) + "as.no"]
    # Wider guesses come last. Every candidate still has to pass the same identity rules in discover().
    if len(joined) <= 40:
        candidates.append(f"{joined}as.no")
    if len(core) >= 2 and core != tokens:
        candidates.append("-".join(core) + ".no")
    trade = [TRADE_STEMS.get(token, token) for token in tokens if token in TRADE_WORDS]
    if trade and core and len("".join(core)) >= 4:
        candidates += [trade[0] + "".join(core) + ".no", "".join(core) + trade[0] + ".no"]
    if len(tokens) >= 3:
        candidates.append("".join(tokens[-2:]) + ".no")
    long_form = "".join(_ascii_tokens(name, long_forms=True))
    if long_form != joined and len(long_form) <= 40:
        candidates.append(f"{long_form}.no")
    return [item for item in dict.fromkeys(candidates) if len(item.split(".")[0]) <= 63][:MAX_NAME_CANDIDATES]


def resolves(domain: str, timeout: float = 4.0) -> bool:
    """True when the name resolves within the timeout. getaddrinfo itself has no deadline, so it runs in a daemon thread."""
    answer: list[bool] = []

    def lookup() -> None:
        try:
            socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
            answer.append(True)
        except (OSError, UnicodeError):
            answer.append(False)

    worker = threading.Thread(target=lookup, daemon=True)
    worker.start()
    worker.join(timeout)
    return bool(answer and answer[0])


def _org_number_proven(website: dict[str, Any], org: str) -> bool:
    value = website.get("value") or {}
    return bool(value.get("org_number_found_at")) and org == re.sub(r"\D", "", str(value.get("org_number_found") or ""))


def _leader_on_site(profile: dict[str, Any], value: dict[str, Any]) -> bool:
    """True when the registered daily manager or board chair is named in full on the site's own pages."""
    roles = ((profile.get("evidence") or {}).get("roles") or {}).get("value") or {}
    names = {str(role.get("name") or "") for role in roles.get("roles") or [] if role.get("role_code") in {"DAGL", "LEDE"} and not role.get("organisation_number") and not role.get("inactive")}
    pages = [value.get("title"), value.get("description"), value.get("main_text_excerpt"), *[page.get("main_text_excerpt") for page in value.get("pages") or []]]
    text = " ".join(_fold(str(page)) for page in pages if page)
    for name in names:
        parts = re.findall(r"[a-z0-9]+", _fold(name))
        if len(parts) < 2 or len("".join(parts)) < 8:
            continue  # a short name is too likely to appear by chance
        for form in {" ".join(parts), f"{parts[0]} {parts[-1]}"}:
            if re.search(rf"(?<![a-z0-9]){re.escape(form)}(?![a-z0-9])", text):
                return True
    return False


def discover(
    profile: dict[str, Any],
    registered_email: Any,
    subunit_sites: list[str] | None = None,
    extra_sites: list[str] | None = None,
    fetch: Callable[..., tuple[dict[str, Any], dict[str, Any]]] = fetch_website,
    dns: Callable[[str], bool] = resolves,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Return (website evidence, metrics); evidence is None unless a candidate was proven exact."""
    org = re.sub(r"\D", "", str(profile.get("organisation_number") or ""))
    metrics: dict[str, Any] = {"requests": 0, "bytes": 0, "latencies_ms": [], "candidates": []}
    domain = email_domain(registered_email)
    subunit_domains = [re.sub(r"^https?://", "", item.strip().casefold()).split("/")[0].removeprefix("www.") for item in (subunit_sites or []) if item and item.strip()]
    extra_domains = [re.sub(r"^https?://", "", item.strip().casefold()).split("/")[0].removeprefix("www.") for item in (extra_sites or []) if item and item.strip()]
    ordered = [("wikidata_official_website", item) for item in dict.fromkeys(extra_domains)][:2] + ([("registered_email_domain", domain)] if domain else []) + [("registered_subunit_website", item) for item in dict.fromkeys(subunit_domains)][:2] + [("legal_name_guess", item) for item in name_candidates(str(profile.get("name") or ""))]
    tried: set[str] = set()
    deadline = time.monotonic() + DISCOVERY_BUDGET_SECONDS
    for origin, candidate in ordered:
        if candidate in tried:
            continue
        if time.monotonic() > deadline:
            metrics["candidates"].append({"domain": candidate, "origin": origin, "outcome": "not_tried_budget"})
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
        address = bool((gated.get("value") or {}).get("registered_address_found_at"))
        phone = bool((gated.get("value") or {}).get("registered_phone_found_at"))
        leader = _leader_on_site(profile, gated.get("value") or {})
        all_name_tokens = float(assessment.get("score") or 0) >= 0.95
        name_is_label = candidate.split(".")[0].replace("-", "") == "".join(_ascii_tokens(str(profile.get("name") or "")))
        if origin in {"registered_email_domain", "registered_subunit_website", "wikidata_official_website"}:
            value = gated.get("value") or {}
            substantive = len(str(value.get("main_text_excerpt") or "").strip()) >= 100
            parked = any("parked" in str(reason) for reason in assessment.get("reasons") or [])
            page_words = set(_tokens(" ".join(str(value.get(key) or "") for key in ("title", "description", "main_text_excerpt"))))
            name_on_page = any(len(token) >= 4 and token in page_words for token in _tokens(profile.get("name")))
            name_is_domain = substantive and not parked and name_is_label and name_on_page
            accepted = proven or name_is_domain or bool(assessment.get("publishable")) or ((phone or leader) and (all_name_tokens or name_is_label)) or (address and name_is_label)  # address alone is not enough: holding companies often register c/o their accountant, whose email domain this may be
        else:
            accepted = proven or ((address or phone or leader) and (all_name_tokens or name_is_label))
        final_url = str((gated.get("value") or {}).get("final_url") or "")
        final_domain = _registered_domain(final_url) if final_url else candidate
        redirected = final_domain != _registered_domain("https://" + candidate)
        if accepted and redirected and not (proven or all_name_tokens):
            accepted = False  # the page we landed on must itself name this company; a shared address or leader only shows the two are related
        if accepted and not proven and re.search(r"\bholding\b", _fold(str(profile.get("name") or ""))):
            accepted = False  # a holding company shares its name and address with the operating company whose site this usually is
        if accepted and not proven and not final_domain.endswith(".no"):
            accepted = False  # an international domain is often the foreign parent's site: only the exact org number counts
        metrics["candidates"].append({"domain": candidate, "origin": origin, "outcome": "accepted" if accepted else "rejected_identity", "org_number_on_site": proven, "registered_address_on_site": address, "registered_phone_on_site": phone, "registered_leader_on_site": leader, "identity_score": assessment.get("score")})
        if accepted:
            value = gated["value"]
            value["discovery"] = {"origin": origin, "candidate": candidate, "org_number_on_site": proven, "registered_address_on_site": address, "registered_phone_on_site": phone, "registered_leader_on_site": leader, "method": "registry_derived_candidate_v3"}
            basis = []
            if proven:
                basis.append("the exact organisation number appears on the site")
            if address:
                basis.append("the registered business address appears on the site")
            if phone:
                basis.append("a registered phone number appears on the site")
            if leader:
                basis.append("the registered daily manager or board chair is named on the site")
            if name_is_label:
                basis.append(f"the domain {candidate} equals the legal name")
            elif all_name_tokens:
                basis.append("all legal-name tokens appear in the homepage identity evidence")
            final_host = urllib.parse.urlparse(value.get("final_url") or "").hostname or ""
            if final_host and final_host.removeprefix("www.") != candidate:
                basis.append(f"{candidate} redirects to this site")
            value["identity_assessment"] = {**assessment, "publishable": True, "status": "exact", "reasons": basis or list(assessment.get("reasons") or [])}
            gated["source_class"] = "company_owned_discovered"
            gated["source_type"] = "company_owned_discovered"
            return gated, metrics
    return None, metrics
