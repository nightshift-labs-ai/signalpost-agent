"""Regression tests for the identity rules added to the starter kit (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.changes import apply_previous, diff  # noqa: E402
from norway_company_agent.claims import build_claims  # noqa: E402
from norway_company_agent.discover_site import discover, email_domain, name_candidates  # noqa: E402
from norway_company_agent.identity import assess_social_identity, assess_website_identity  # noqa: E402
from norway_company_agent.website import find_address, find_org_number  # noqa: E402

ORG = "985589003"
PROFILE = {"organisation_number": ORG, "name": "GANGSTAD BYGG AS", "evidence": {}}


def site(url: str, *, org_found: bool = False, title: str = "", text: str = "") -> dict:
    value = {"final_url": url, "title": title, "description": "", "main_text_excerpt": text, "pages": [], "social_links": []}
    if org_found:
        value.update({"org_number_found": ORG, "org_number_found_at": url, "identity_text_excerpt": f"Org.nr {ORG}"})
    return {"status": "available", "value": value, "source_url": url}


def fake_fetch(sites: dict):
    def fetch(domain):
        return sites.get(domain, {"status": "not_found"}), {"requests": 1, "bytes": 0, "latencies_ms": []}
    return fetch


def test_email_filters_webmail_and_keeps_company_domain():
    assert email_domain("post@bekk.no") == "bekk.no"
    assert email_domain("a@gmail.com") is None
    assert email_domain("not an address") is None


def test_name_candidates_refuse_short_single_token():
    assert name_candidates("ABC AS") == []
    assert name_candidates("GANGSTAD BYGG AS")[0] == "gangstadbygg.no"


def test_guess_needs_proof_and_never_trusts_a_name_match_alone():
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", title="Gangstad Bygg AS", text="x" * 200)}
    found, _ = discover(PROFILE, None, fetch=fake_fetch(sites), dns=lambda _: True)
    assert found is None


def test_guess_with_org_number_is_accepted():
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", org_found=True)}
    found, _ = discover(PROFILE, None, fetch=fake_fetch(sites), dns=lambda _: True)
    assert found["value"]["discovery"]["origin"] == "legal_name_guess"


def test_email_domain_equal_to_name_is_accepted():
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", title="Velkommen", text="Vi bygger hus. " * 20)}
    found, _ = discover(PROFILE, "post@gangstadbygg.no", fetch=fake_fetch(sites), dns=lambda _: True)
    assert found is not None


def test_parked_or_empty_email_domain_is_rejected_even_when_name_matches():
    for sites in (
        {"gangstadbygg.no": site("https://gangstadbygg.no/", title="gangstadbygg.no is parked", text="x" * 200)},
        {"gangstadbygg.no": site("https://gangstadbygg.no/", title="", text="")},
    ):
        found, _ = discover(PROFILE, "post@gangstadbygg.no", fetch=fake_fetch(sites), dns=lambda _: True)
        assert found is None


def test_accountant_email_domain_is_rejected_even_at_the_same_address():
    site_value = site("https://devoldsregnskap.no/", title="Devold Regnskap")
    site_value["value"]["registered_address_found_at"] = "https://devoldsregnskap.no/"
    found, metrics = discover(PROFILE, "post@devoldsregnskap.no", fetch=fake_fetch({"devoldsregnskap.no": site_value}), dns=lambda domain: domain == "devoldsregnskap.no")
    assert found is None
    assert metrics["candidates"][0]["outcome"] == "rejected_identity"


def test_org_number_match_is_exact():
    assert find_org_number("Org.nr: 981 566 378 MVA", "981566378")
    assert find_org_number("Telefon 981 566 379", "981566378") is None


def test_address_needs_postcode_near_street():
    assert find_address("Storgata 12, 0150 OSLO", {"postcode": "0150", "street": "Storgata"})
    assert find_address("0150 OSLO" + " filler" * 40 + " Storgata", {"postcode": "0150", "street": "Storgata"}) is None


def test_group_member_does_not_get_the_registry_domain_anchor():
    profile = {"organisation_number": ORG, "name": "BEKK HOLDING AS", "website": "www.bekk.no", "evidence": {"registry_live": {"value": {"website": "www.bekk.no", "in_group": True}}, "website": {"value": {"final_url": "https://www.bekk.no/", "title": "Bekk", "pages": []}}}}
    assert assess_website_identity(profile)["publishable"] is False
    profile["evidence"]["registry_live"]["value"]["in_group"] = False
    assert assess_website_identity(profile)["publishable"] is True


def test_social_handle_needs_token_shared_with_site_domain():
    profile = {"name": "BEKK CONSULTING AS"}
    assert assess_social_identity(profile, {"url": "https://linkedin.com/company/bekk"}, "www.bekk.no")["publishable"] is True
    assert assess_social_identity(profile, {"url": "https://linkedin.com/company/bekk"}, "www.other.no")["publishable"] is False


def test_unverified_site_yields_ambiguous_and_no_site_facts():
    profile = {"organisation_number": ORG, "name": "X AS", "evidence": {"website": {"status": "available", "source_url": "https://x.no", "value": {"identity_assessment": {"publishable": False, "reasons": ["weak"]}, "final_url": "https://x.no", "description": "marketing"}}}}
    claims, _, _ = build_claims(profile)
    assert {"official_website", "hiring.job_posting"} <= {c["field"] for c in claims if c["availability"] == "ambiguous"}
    assert not [c for c in claims if c["field"] == "self_description"]


def test_absence_is_not_zero():
    claims, _, _ = build_claims({"organisation_number": ORG, "name": "X AS", "evidence": {"registry_live": {"status": "available", "source_url": "u", "value": {"employees": None}}}})
    employees = next(c for c in claims if c["field"] == "registered_employees")
    assert employees["value"] is None and employees["availability"] == "not_available"


def test_failed_refresh_is_not_a_removal():
    old = [{"field": "official_website", "value": "x", "availability": "available"}]
    new = [{"field": "official_website", "value": None, "availability": "failed"}]
    assert diff(new, old) == []


def test_refresh_is_idempotent_and_keeps_evidence_on_change():
    claims = [{"field": "a", "value": 1, "availability": "available"}]
    same = apply_previous({"claims": claims, "run_id": "r2"}, {"claims": claims, "run_id": "r1", "evidence": [{"id": "ev-1"}]})
    assert same["changes"] == [] and same["prior_evidence"] == []
    changed = apply_previous({"claims": [{"field": "a", "value": 2, "availability": "available"}], "run_id": "r2"}, {"claims": claims, "run_id": "r1", "evidence": [{"id": "ev-1"}]})
    assert changed["changes"][0]["type"] == "changed"
    assert changed["prior_evidence"][0]["id"] == "ev-1@r1"


def test_workplace_diff_uses_a_stable_key_and_ignores_empty_new_fields():
    old = [{"field": "registered_workplace", "availability": "available", "value": {"organisation_number": "1", "name": "A"}}]
    same = [{"field": "registered_workplace", "availability": "available", "value": {"organisation_number": "1", "name": "A", "website": None}}]
    renamed = [{"field": "registered_workplace", "availability": "available", "value": {"organisation_number": "1", "name": "B"}}]
    assert diff(same, old) == []
    assert [c["type"] for c in diff(renamed, old)] == ["changed"]


def test_international_domain_needs_the_organisation_number():
    profile = {"organisation_number": ORG, "name": "SMITH NEPHEW AS", "evidence": {}}
    text = "Smith Nephew medical devices. " * 10
    found, metrics = discover(profile, "post@smith-nephew.com", fetch=fake_fetch({"smith-nephew.com": site("https://www.smith-nephew.com/", title="Smith Nephew", text=text)}), dns=lambda domain: domain == "smith-nephew.com")
    assert found is None and metrics["candidates"][0]["outcome"] == "rejected_identity"
    found, _ = discover(profile, "post@smith-nephew.com", fetch=fake_fetch({"smith-nephew.com": site("https://www.smith-nephew.com/", title="Smith Nephew", text=text, org_found=True)}), dns=lambda domain: domain == "smith-nephew.com")
    assert found is not None


def test_sitemap_news_with_lastmod_become_dated_claims():
    from norway_company_agent.website import _fetch_text  # noqa: F401 (imports must resolve)
    sitemap = {"source_url": "https://a.no/sitemap.xml", "content_sha256": "x", "news": [{"title": "ny avtale", "url": "https://a.no/nyheter/ny-avtale", "date": "2026-10-02", "source_url": "https://a.no/sitemap.xml", "date_basis": "sitemap_lastmod"}], "jobs": [{"title": "utvikler", "posting_url": "https://a.no/karriere/utvikler", "sitemap_lastmod": "2026-09-01", "date_basis": "sitemap_lastmod"}]}
    profile = {"organisation_number": ORG, "name": "X AS", "evidence": {"website": {"status": "available", "source_url": "https://a.no", "retrieved_at": "t", "value": {"final_url": "https://a.no/", "identity_assessment": {"publishable": True, "score": 1.0}, "pages": [], "social_links": [], "sitemap": sitemap}}}}
    claims, rows, _ = build_claims(profile)
    news = [c for c in claims if c["field"] == "public_activity.dated_item" and c["availability"] == "available"]
    assert news[0]["evidence_ids"] == ["ev-sitemap"] and "lastmod" in news[0]["source_note"]
    assert any(c["field"] == "hiring.job_posting" and c["availability"] == "available" for c in claims)
    assert any(row["id"] == "ev-sitemap" for row in rows)
    assert not [c for c in claims if c["field"] == "hiring.job_posting" and c["availability"] == "not_available"]


def test_sitemap_page_type_children_are_not_news():
    from norway_company_agent.website import SITEMAP_CHILD, SITEMAP_NOT_NEWS
    keep = lambda url: bool(SITEMAP_CHILD.search(url)) and not SITEMAP_NOT_NEWS.search(url)
    assert keep("https://a.no/wp-sitemap-posts-post-1.xml")
    assert keep("https://a.no/nyhet-sitemap.xml")
    assert not keep("https://a.no/wp-sitemap-posts-page-1.xml")
    assert not keep("https://a.no/page-sitemap.xml")
