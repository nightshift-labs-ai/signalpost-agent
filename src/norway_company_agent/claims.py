"""Turn a fetched profile into contract claims: one explicit state per information type.

Every published value points at an evidence record. Nothing is inferred: absence stays absent,
and a company site that did not pass the identity gate yields `ambiguous`, never a claim.
"""
from __future__ import annotations

from typing import Any

STATE = {"available": "available", "not_found": "not_available", "not_applicable": "not_applicable", "blocked": "blocked", "source_error": "failed", "not_fetched": "failed"}
OFFICIAL_MODULES = ("registry_live", "financials", "financial_history", "roles", "group", "locations")
PLATFORMS = ("linkedin", "facebook", "instagram", "youtube", "x", "tiktok")
CONFIDENCE = {"official": 0.99, "site": 0.9}


def _evidence_rows(profile: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for module, record in (profile.get("evidence") or {}).items():
        if not isinstance(record, dict) or not record.get("source_url"):
            continue
        rows.append({
            "id": f"ev-{module}",
            "source_url": record["source_url"],
            "source_class": record.get("source_class") or record.get("source_type"),
            "retrieved_at": record.get("retrieved_at"),
            "content_sha256": record.get("content_sha256"),
            "effective_at": record.get("effective_at"),
            "status": record.get("status"),
        })
    return rows


def _site_page_evidence(site: dict[str, Any], retrieved_at: Any) -> list[dict[str, Any]]:
    out = []
    for index, page in enumerate(site.get("pages") or []):
        out.append({
            "id": f"ev-site-{index}",
            "source_url": page.get("url"),
            "source_class": "company_owned",
            "retrieved_at": retrieved_at,
            "content_sha256": page.get("content_sha256"),
            "claim_span": page.get("org_number_excerpt") or (page.get("title") or "")[:200],
        })
    return out


def _claim(field: str, value: Any, availability: str, confidence: float | None, evidence_ids: list[str], **extra: Any) -> dict[str, Any]:
    return {"field": field, "value": value, "availability": availability, "confidence": confidence, "evidence_ids": evidence_ids, **extra}


def _state_of(record: dict[str, Any] | None) -> str:
    return STATE.get((record or {}).get("status"), "failed")


def build_claims(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    ev = profile.get("evidence") or {}
    claims: list[dict[str, Any]] = []
    evidence_rows = _evidence_rows(profile)

    live = ev.get("registry_live") or {}
    value = live.get("value") or {}
    state = _state_of(live)
    for field, key in (("legal_name", "name"), ("legal_form", "legal_form"), ("registered_employees", "employees"), ("business_address", "business_address"), ("industry", "industry")):
        present = value.get(key) not in (None, "", {})
        claims.append(_claim(field, value.get(key) if present else None, ("available" if present else "not_available") if state == "available" else state, CONFIDENCE["official"] if present else None, ["ev-registry_live"]))

    fin = ev.get("financials") or {}
    rows = (fin.get("value") or {}).get("records") or []
    if rows:
        for row in rows:
            period = row.get("period") or {}
            for field in ("revenue", "operating_result", "profit_before_tax", "annual_result", "assets", "equity", "debt"):
                if row.get(field) is not None:
                    claims.append(_claim(f"financials.{field}", row[field], "available", CONFIDENCE["official"], ["ev-financials"], currency=row.get("currency"), reporting_period=period, account_type=row.get("account_type")))
    else:
        claims.append(_claim("financials", None, "not_available" if _state_of(fin) == "available" else _state_of(fin), None, ["ev-financials"]))

    years = (ev.get("financial_history") or {}).get("value") or {}
    if years.get("years"):
        claims.append(_claim("financials.filed_years", years["years"], "available", CONFIDENCE["official"], ["ev-financial_history"]))

    roles_record = ev.get("roles") or {}
    roles = [r for r in ((roles_record.get("value") or {}).get("roles") or []) if not r.get("inactive") and r.get("name")]
    if roles:
        for role in roles:
            claims.append(_claim("leadership.role", {"name": role["name"], "role": role.get("role"), "role_code": role.get("role_code"), "organisation_number": role.get("organisation_number")}, "available", CONFIDENCE["official"], ["ev-roles"], effective_at=role.get("last_changed")))
    else:
        claims.append(_claim("leadership.role", None, "not_available" if _state_of(roles_record) == "available" else _state_of(roles_record), None, ["ev-roles"]))

    locations_record = ev.get("locations") or {}
    locations = (locations_record.get("value") or {}).get("locations") or []
    if locations:
        for item in locations:
            claims.append(_claim("registered_workplace", item, "available", CONFIDENCE["official"], ["ev-locations"]))
    else:
        claims.append(_claim("registered_workplace", None, "not_available" if _state_of(locations_record) == "available" else _state_of(locations_record), None, ["ev-locations"]))

    website = ev.get("website") or {}
    site = website.get("value") or {}
    assessment = site.get("identity_assessment") or {}
    publishable = website.get("status") == "available" and bool(assessment.get("publishable"))
    if publishable:
        page_rows = _site_page_evidence(site, website.get("retrieved_at"))
        evidence_rows.extend(page_rows)
        proof_ids = [row["id"] for row in page_rows if row.get("claim_span") and "ev-site" in row["id"]][:1] or ["ev-website"]
        claims.append(_claim("official_website", site.get("final_url"), "available", assessment.get("score"), ["ev-website", *proof_ids], identity_method=assessment.get("method"), identity_reasons=assessment.get("reasons"), discovery=site.get("discovery"), org_number_found_at=site.get("org_number_found_at")))
        if site.get("description"):
            claims.append(_claim("self_description", site["description"], "available", CONFIDENCE["site"], ["ev-website"], source_note="company-owned promotional text, not independent"))
        found = {item["platform"]: item["url"] for item in site.get("social_links") or []}
        for platform in PLATFORMS:
            claims.append(_claim(f"company_profile.{platform}", found.get(platform), "available" if platform in found else "not_available", CONFIDENCE["site"] if platform in found else None, ["ev-website"]))
        sitemap = site.get("sitemap") or {}
        if sitemap.get("source_url"):
            evidence_rows.append({"id": "ev-sitemap", "source_url": sitemap["source_url"], "source_class": "company_owned", "retrieved_at": website.get("retrieved_at"), "content_sha256": sitemap.get("content_sha256")})
        jobs = [(idx, job) for idx, page in enumerate(site.get("pages") or []) for job in [*(page.get("job_postings") or []), *(page.get("listed_jobs") or [])]]
        for idx, job in jobs:
            claims.append(_claim("hiring.job_posting", job, "available", CONFIDENCE["site"], [f"ev-site-{idx}"], effective_at=job.get("date_posted")))
        listed_urls = {job.get("posting_url") for _, job in jobs}
        sitemap_jobs = [job for job in sitemap.get("jobs") or [] if job.get("posting_url") not in listed_urls]
        for job in sitemap_jobs:
            claims.append(_claim("hiring.job_posting", job, "available", CONFIDENCE["site"], ["ev-sitemap"], effective_at=job.get("sitemap_lastmod"), source_note="listed in the company's own sitemap; lastmod is the page's last change, not a posting date"))
        jobs = jobs + [(None, job) for job in sitemap_jobs]
        job_pages = [idx for idx, page in enumerate(site.get("pages") or []) if page.get("kind") == "jobs"]
        if not jobs:
            claims.append(_claim("hiring.job_posting", None, "not_available", None, [f"ev-site-{idx}" for idx in job_pages] or ["ev-website"]))
        if job_pages:
            page = site["pages"][job_pages[0]]
            claims.append(_claim("hiring.careers_page", page.get("url"), "available", CONFIDENCE["site"], [f"ev-site-{job_pages[0]}"]))
        activity = [(idx, page) for idx, page in enumerate(site.get("pages") or []) if page.get("kind") == "news"]
        dated = [(idx, {"title": item["title"], "url": item["url"], "date": item["date"]}) for idx, page in activity for item in page.get("news_items") or []]
        seen_urls = {item["url"] for _, item in dated}
        dated += [(idx, {"title": (page.get("title") or "")[:300], "url": page.get("url"), "date": date}) for idx, page in activity for date in page.get("stated_dates") or [] if page.get("url") not in seen_urls]
        for idx, item in dated:
            claims.append(_claim("public_activity.dated_item", item, "available", CONFIDENCE["site"], [f"ev-site-{idx}"], effective_at=item["date"], source_note="company-owned"))
        seen_urls |= {item["url"] for _, item in dated}
        sitemap_news = [item for item in sitemap.get("news") or [] if item["url"] not in seen_urls]
        for item in sitemap_news:
            claims.append(_claim("public_activity.dated_item", item, "available", CONFIDENCE["site"], ["ev-sitemap"], effective_at=item["date"], source_note="company-owned; date is the sitemap lastmod, which can be a later edit rather than first publication"))
        dated = dated + [(None, item) for item in sitemap_news]
        if not dated:
            claims.append(_claim("public_activity.dated_item", None, "not_available", None, [f"ev-site-{idx}" for idx, _ in activity] or ["ev-website"]))
    else:
        site_state = "ambiguous" if website.get("status") == "available" else _state_of(website)
        for field in ("official_website", "hiring.job_posting", "public_activity.dated_item"):
            claims.append(_claim(field, None, site_state, None, ["ev-website"], identity_reasons=assessment.get("reasons")))

    return claims, evidence_rows, build_summary(profile, claims, publishable)


def _money(value: Any, currency: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:,.0f} {currency or ''}".strip()


def build_summary(profile: dict[str, Any], claims: list[dict[str, Any]], publishable: bool) -> dict[str, Any]:
    """A deterministic summary. Each sentence names the evidence behind it; nothing is inferred."""
    live = (profile.get("evidence") or {}).get("registry_live", {}).get("value") or {}
    industry = (live.get("industry") or {}).get("beskrivelse") or profile.get("industry_label")
    legal_form = profile.get("legal_form") or live.get("legal_form") or "entity"
    sentences: list[dict[str, Any]] = []

    def say(text: str, *evidence_ids: str) -> None:
        sentences.append({"text": text, "evidence_ids": list(evidence_ids)})

    say(f"{profile.get('name')} is a Norwegian {legal_form}" + (f" registered under the industry '{industry}'." if industry else "."), "ev-registry_live")
    if live.get("registered_purpose"):
        say(f"Its registered purpose is: {live['registered_purpose'][:280]}", "ev-registry_live")
    if live.get("employees") is not None:
        say(f"It reports {live['employees']} employees in the registry.", "ev-registry_live")
    for claim in claims:
        if claim["field"] == "financials.revenue":
            period = (claim.get("reporting_period") or {}).get("tilDato") or "the latest filed year"
            line = f"Filed revenue for the period ending {period} was {_money(claim['value'], claim.get('currency'))}"
            result = next((c for c in claims if c["field"] == "financials.annual_result" and c.get("reporting_period") == claim.get("reporting_period")), None)
            say(line + (f", with an annual result of {_money(result['value'], result.get('currency'))}." if result else "."), "ev-financials")
            break
    leader = next((c["value"] for c in claims if c["field"] == "leadership.role" and isinstance(c.get("value"), dict) and c["value"].get("role_code") == "DAGL"), None) or next((c["value"] for c in claims if c["field"] == "leadership.role" and isinstance(c.get("value"), dict) and c["value"].get("role_code") == "LEDE"), None)
    if leader:
        say(f"{leader.get('name')} is registered as {str(leader.get('role') or 'a role holder').lower()}.", "ev-roles")
    workplaces = sum(1 for c in claims if c["field"] == "registered_workplace" and c["availability"] == "available")
    if workplaces:
        say(f"It has {workplaces} registered workplace(s).", "ev-locations")
    website = next((c for c in claims if c["field"] == "official_website"), {})
    if publishable:
        say(f"Its official website is {website.get('value')}, confirmed by: {'; '.join(website.get('identity_reasons') or ['site identity checks'])}.", "ev-website")
    else:
        say("No website could be confirmed as this exact entity, so no site-based facts are published.", "ev-website")
    gaps = sorted({c["field"].split(".")[0] for c in claims if c["availability"] != "available"})
    say("Not confirmed in the checked sources: " + (", ".join(gaps) if gaps else "nothing") + ".")
    return {"text": " ".join(item["text"] for item in sentences), "sentences": sentences}
