"""Turn a fetched profile into contract claims: one explicit state per information type.

Every published value points at an evidence record. Nothing is inferred: absence stays absent,
and a company site that did not pass the identity gate yields `ambiguous`, never a claim.
"""
from __future__ import annotations

import urllib.parse

import re
from typing import Any

from .identity import _structured_names

STATE = {"available": "available", "not_found": "not_available", "not_applicable": "not_applicable", "blocked": "blocked", "source_error": "failed", "not_fetched": "failed"}
OFFICIAL_MODULES = ("registry_live", "financials", "financial_history", "roles", "group", "locations")
PLATFORMS = ("linkedin", "facebook", "instagram", "youtube", "x", "tiktok")
CONFIDENCE = {"official": 0.99, "site": 0.9}
EXTRACTION_METHOD = {"registry": "brreg_bulk_csv", "accounting_obligation": "rule_from_registry_row", "website": "html_fetch"}
BOILERPLATE = re.compile(r"powered by|just another wordpress|smugmug|squarespace|wix\.com|min nye (?:side|nettside)|coming soon|kommer snart", re.I)


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
            "extraction_method": EXTRACTION_METHOD.get(module, "brreg_api_json"),
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
            "claim_span": page.get("org_number_excerpt") or page.get("address_excerpt") or page.get("phone_excerpt") or (page.get("title") or "")[:200],
            "extraction_method": "html_text_match",
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

    group_links = ((ev.get("group") or {}).get("value") or {}).get("children") or []
    for link in group_links:
        if str(link.get("organisasjonsnummer") or "") and str(link.get("organisasjonsnummer")) != str(profile.get("organisation_number")):
            relation = link.get("knytningsform") or {}
            claims.append(_claim("group.link", {"organisation_number": link.get("organisasjonsnummer"), "name": link.get("navn"), "relationship": relation.get("beskrivelse"), "relationship_code": relation.get("kode"), "since": link.get("dato")}, "available", CONFIDENCE["official"], ["ev-group"]))

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
        deciding_urls = [site.get("org_number_found_at"), site.get("registered_address_found_at"), site.get("registered_phone_found_at")]
        proof_ids = [row["id"] for url in deciding_urls for row in page_rows if url and row.get("source_url") == url][:1] or [row["id"] for row in page_rows if row.get("claim_span")][:1] or ["ev-website"]
        claims.append(_claim("official_website", site.get("final_url"), "available", assessment.get("score"), ["ev-website", *proof_ids], identity_method=assessment.get("method"), identity_reasons=assessment.get("reasons"), discovery=site.get("discovery"), org_number_found_at=site.get("org_number_found_at")))
        brand_names = [name.strip() for name in _structured_names(site.get("structured_organisations") or []) if name and name.strip()]
        claims.append(_claim("public_brand", brand_names[0][:120] if brand_names else None, "available" if brand_names else "not_available", CONFIDENCE["site"] if brand_names else None, ["ev-website"]))
        if site.get("description") and not BOILERPLATE.search(str(site["description"])) and not _only_repeats_name(site["description"], profile.get("name")):
            claims.append(_claim("self_description", site["description"], "available", CONFIDENCE["site"], ["ev-website"], source_note="company-owned promotional text, not independent"))
        found = {item["platform"]: item["url"] for item in site.get("social_links") or []}
        for platform in PLATFORMS:
            claims.append(_claim(f"company_profile.{platform}", found.get(platform), "available" if platform in found else "not_available", CONFIDENCE["site"] if platform in found else None, ["ev-website"]))
        sitemap = site.get("sitemap") or {}
        sitemap_rows = {}
        for index, document in enumerate(sitemap.get("documents") or []):
            sitemap_rows[document["url"]] = f"ev-sitemap-{index}"
            evidence_rows.append({"id": f"ev-sitemap-{index}", "source_url": document["url"], "source_class": "company_owned", "retrieved_at": website.get("retrieved_at"), "content_sha256": document.get("sha256"), "extraction_method": "sitemap_xml"})
        if sitemap.get("source_url") and not sitemap_rows:
            sitemap_rows[sitemap["source_url"]] = "ev-sitemap"
            evidence_rows.append({"id": "ev-sitemap", "source_url": sitemap["source_url"], "source_class": "company_owned", "retrieved_at": website.get("retrieved_at"), "content_sha256": sitemap.get("content_sha256"), "extraction_method": "sitemap_xml"})
        jobs = [(idx, job) for idx, page in enumerate(site.get("pages") or []) for job in [*(page.get("job_postings") or []), *(page.get("listed_jobs") or [])]]
        for idx, job in jobs:
            claims.append(_claim("hiring.job_posting", job, "available", CONFIDENCE["site"], [f"ev-site-{idx}"], effective_at=job.get("date_posted")))
        listed_urls = {job.get("posting_url") for _, job in jobs}
        sitemap_jobs = [job for job in sitemap.get("jobs") or [] if job.get("posting_url") not in listed_urls]
        for job in sitemap_jobs:
            claims.append(_claim("hiring.job_posting", job, "available", CONFIDENCE["site"], [sitemap_rows.get(job.get("source_url"), "ev-sitemap")], effective_at=job.get("sitemap_lastmod"), source_note="listed in the company's own sitemap; lastmod is the page's last change, not a posting date"))
        jobs = jobs + [(None, job) for job in sitemap_jobs]
        job_pages = [idx for idx, page in enumerate(site.get("pages") or []) if page.get("kind") == "jobs"]
        if not jobs:
            claims.append(_claim("hiring.job_posting", None, "not_available", None, [f"ev-site-{idx}" for idx in job_pages] or ["ev-website"], source_checked=True))
        if job_pages:
            page = site["pages"][job_pages[0]]
            claims.append(_claim("hiring.careers_page", page.get("url"), "available", CONFIDENCE["site"], [f"ev-site-{job_pages[0]}"]))
        board_links = {link["url"]: (idx, link) for idx in job_pages for link in site["pages"][idx].get("job_board_links") or []}
        for idx, link in board_links.values():
            claims.append(_claim("hiring.job_board_link", link, "available", CONFIDENCE["site"], [f"ev-site-{idx}"], source_note="linked from the company's own careers page; the destination was not fetched"))
        activity = [(idx, page) for idx, page in enumerate(site.get("pages") or []) if page.get("kind") == "news"]
        dated = [(idx, {"title": item["title"], "url": item["url"], "date": item["date"]}) for idx, page in activity for item in page.get("news_items") or []]
        seen_urls = {item["url"] for _, item in dated}
        dated += [(idx, {"title": (page.get("title") or "")[:300], "url": page.get("url"), "date": date}) for idx, page in activity if _is_article_page(page) for date in page["stated_dates"] if page.get("url") not in seen_urls]
        for idx, item in dated:
            claims.append(_claim("public_activity.dated_item", item, "available", CONFIDENCE["site"], [f"ev-site-{idx}"], effective_at=item["date"], source_note="company-owned"))
        seen_urls |= {item["url"] for _, item in dated}
        feed = site.get("feed") or {}
        feed_news = [item for item in feed.get("items") or [] if item["url"] not in seen_urls and not _is_product_code(item.get("title"))]
        if feed_news:
            evidence_rows.append({"id": "ev-feed", "source_url": feed.get("source_url"), "source_class": "company_owned", "retrieved_at": website.get("retrieved_at"), "content_sha256": feed.get("content_sha256"), "extraction_method": "rss_atom_feed"})
        for item in feed_news:
            claims.append(_claim("public_activity.dated_item", item, "available", CONFIDENCE["site"], ["ev-feed"], effective_at=item["date"], source_note="company-owned feed; the date is the post's publication date"))
        dated = dated + [(None, item) for item in feed_news]
        seen_urls |= {item["url"] for item in feed_news}
        sitemap_news = [item for item in sitemap.get("news") or [] if item["url"] not in seen_urls]
        for item in sitemap_news:
            note = "company-owned; the date is in the page's URL" if item.get("date_basis") == "date_in_url" else "company-owned; date is the sitemap lastmod, which can be a later edit rather than first publication"
            claims.append(_claim("public_activity.dated_item", item, "available", CONFIDENCE["site"], [sitemap_rows.get(item.get("source_url"), "ev-sitemap")], effective_at=item["date"], source_note=note))
        dated = dated + [(None, item) for item in sitemap_news]
        if not dated:
            claims.append(_claim("public_activity.dated_item", None, "not_available", None, [f"ev-site-{idx}" for idx, _ in activity] or ["ev-website"], source_checked=True))
    else:
        site_state = "ambiguous" if website.get("status") == "available" else _state_of(website)
        for field in ("official_website", "public_brand", "self_description", "hiring.job_posting", "hiring.careers_page", "public_activity.dated_item", *[f"company_profile.{platform}" for platform in PLATFORMS]):
            claims.append(_claim(field, None, site_state, None, ["ev-website"], identity_reasons=assessment.get("reasons"), source_checked=False))

    return claims, evidence_rows, build_summary(profile, claims, publishable)


def _money(value: Any, currency: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:,.0f} {currency or ''}".strip()


def _only_repeats_name(description: Any, name: Any) -> bool:
    """A description that is just the company's name says nothing about what it does."""
    words = lambda text: set(re.findall(r"[^\W_]+", str(text or "").casefold())) - {"as", "asa", "ans", "da", "ba", "sa"}
    described = words(description)
    return bool(described) and described <= words(name)


def _is_article_page(page: dict[str, Any]) -> bool:
    """A page's own date describes it only when it has one date and sits below a section: several dates or a top-level path mean a listing."""
    depth = len([part for part in urllib.parse.urlparse(str(page.get("url") or "")).path.split("/") if part])
    return len(page.get("stated_dates") or []) == 1 and depth >= 2


def _is_product_code(title: Any) -> bool:
    """A shop's product feed gives titles such as 750P201T127: dated, but not activity anyone would call news."""
    text = str(title or "").strip()
    return bool(text) and " " not in text and any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text)


def _financial_sentences(claims: list[dict[str, Any]]) -> list[str]:
    """Latest filed numbers first, then the change against the previous filed year of the same account type."""
    def by_period(field: str) -> list[dict[str, Any]]:
        rows = [c for c in claims if c["field"] == field and isinstance(c.get("value"), (int, float)) and (c.get("reporting_period") or {}).get("tilDato")]
        return sorted(rows, key=lambda c: (c["reporting_period"]["tilDato"], c.get("account_type") == "SELSKAP"))

    def in_period(rows: list[dict[str, Any]], anchor: dict[str, Any]) -> dict[str, Any] | None:
        return next((c for c in rows if c.get("reporting_period") == anchor.get("reporting_period") and c.get("account_type") == anchor.get("account_type")), None)

    revenue = by_period("financials.revenue")
    results = by_period("financials.annual_result")
    candidates = [*revenue, *results]
    if not candidates:
        return []
    own = [c for c in candidates if c.get("account_type") != "KONSERN"]  # the company's own accounts before any group accounts
    anchor = max(own or candidates, key=lambda c: (c["reporting_period"]["tilDato"], c["field"] == "financials.revenue"))
    scope = "group " if anchor.get("account_type") == "KONSERN" else ""
    period = anchor["reporting_period"]["tilDato"]
    latest_revenue = in_period(revenue, anchor)
    latest_result = in_period(results, anchor)
    if latest_revenue and latest_result:
        out = [f"Filed {scope}revenue for the period ending {period} was {_money(latest_revenue['value'], latest_revenue.get('currency'))}, with an annual result of {_money(latest_result['value'], latest_result.get('currency'))}."]
    elif latest_revenue:
        out = [f"Filed {scope}revenue for the period ending {period} was {_money(latest_revenue['value'], latest_revenue.get('currency'))}."]
    else:
        out = [f"The filed {scope}annual result for the period ending {period} was {_money(latest_result['value'], latest_result.get('currency'))}; no revenue figure was filed for that period."]

    series = revenue if latest_revenue else results
    earlier = [c for c in series if c["reporting_period"]["tilDato"] < period and c.get("account_type") == anchor.get("account_type")]
    if not earlier:
        return out
    previous = earlier[-1]
    previous_end = previous["reporting_period"]["tilDato"]
    if int(period[:4]) - int(previous_end[:4]) != 1:
        return out  # a gap in the filings: comparing across it would suggest a yearly change that was not reported
    label = "Revenue" if latest_revenue else "The annual result"
    current = float((latest_revenue or latest_result)["value"])
    before = float(previous["value"])
    currency = previous.get("currency")
    percent = f" ({(current - before) / before * 100:+.1f}%)" if before > 0 else ""
    out.append(f"{label} changed by {current - before:+,.0f} {currency or ''}".rstrip() + f"{percent} from {_money(before, currency)} in the period ending {previous_end}.")
    return out


def build_summary(profile: dict[str, Any], claims: list[dict[str, Any]], publishable: bool) -> dict[str, Any]:
    """A deterministic summary. Each sentence names the evidence behind it; nothing is inferred."""
    live = (profile.get("evidence") or {}).get("registry_live", {}).get("value") or {}
    industry = (live.get("industry") or {}).get("beskrivelse") or profile.get("industry_label")
    legal_form = profile.get("legal_form") or live.get("legal_form") or "entity"
    sentences: list[dict[str, Any]] = []

    def say(text: str, *evidence_ids: str) -> None:
        sentences.append({"text": text, "evidence_ids": list(evidence_ids)})

    kind = "a foreign company's Norwegian branch (NUF)" if str(legal_form).upper() == "NUF" else f"a Norwegian {legal_form}"
    say(f"{profile.get('name')} is {kind}" + (f" registered under the industry '{industry}'." if industry else "."), "ev-registry_live")
    if live.get("registered_purpose"):
        say(f"Its registered purpose is: {live['registered_purpose'][:280]}", "ev-registry_live")
    if live.get("employees") is not None:
        say(f"It reports {live['employees']} employees in the registry.", "ev-registry_live")
    for text in _financial_sentences(claims):
        say(text, "ev-financials")
    leader = next((c["value"] for c in claims if c["field"] == "leadership.role" and isinstance(c.get("value"), dict) and c["value"].get("role_code") == "DAGL"), None) or next((c["value"] for c in claims if c["field"] == "leadership.role" and isinstance(c.get("value"), dict) and c["value"].get("role_code") == "LEDE"), None)
    if leader:
        role = {"DAGL": "managing director (daglig leder)", "LEDE": "chair of the board (styrets leder)"}.get(str(leader.get("role_code")), str(leader.get("role") or "a role holder").lower())
        say(f"{leader.get('name')} is registered as {role}.", "ev-roles")
    workplaces = sum(1 for c in claims if c["field"] == "registered_workplace" and c["availability"] == "available")
    if workplaces:
        say(f"It has {workplaces} registered workplace(s).", "ev-locations")
    website = next((c for c in claims if c["field"] == "official_website"), {})
    if publishable:
        say(f"Its official website is {website.get('value')}, confirmed by: {'; '.join(website.get('identity_reasons') or ['site identity checks'])}.", "ev-website")
    else:
        say("No website could be confirmed as this exact entity, so no site-based facts are published.", "ev-website")
    gap_names = {"company_profile": "linked social profiles", "hiring": "hiring information", "official_website": "official website", "public_activity": "dated public activity", "public_brand": "public brand name", "registered_employees": "registered employee count", "self_description": "the company's own description", "leadership": "leadership", "financials": "filed accounts", "registered_workplace": "registered workplaces", "group": "group links"}
    gaps = sorted({gap_names.get(c["field"].split(".")[0], c["field"].split(".")[0].replace("_", " ")) for c in claims if c["availability"] != "available"})
    gap_evidence = sorted({evidence_id for c in claims if c["availability"] != "available" for evidence_id in c.get("evidence_ids") or []})[:4]
    say("Not found or not checked in this run: " + (", ".join(gaps) if gaps else "nothing") + ".", *gap_evidence)
    return {"text": " ".join(item["text"] for item in sentences), "sentences": sentences}
