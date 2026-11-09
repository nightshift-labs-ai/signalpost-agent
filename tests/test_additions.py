"""Regression tests for the identity rules added to the starter kit (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_company_agent.changes import apply_previous, diff  # noqa: E402
from norway_company_agent.claims import build_claims  # noqa: E402
from norway_company_agent.discover_site import discover, email_domain, name_candidates  # noqa: E402
from norway_company_agent.identity import assess_social_identity, assess_website_identity  # noqa: E402
from norway_company_agent.website import _read_capped, find_address, find_org_number, find_phone  # noqa: E402

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
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", title="Velkommen", text="Gangstad Bygg bygger hus. " * 20)}
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


def test_email_domain_equal_to_name_needs_the_name_on_the_page():
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", title="Velkommen", text="Beste nettcasino 2026. " * 20)}
    found, _ = discover(PROFILE, "post@gangstadbygg.no", fetch=fake_fetch(sites), dns=lambda domain: domain == "gangstadbygg.no")
    assert found is None


def test_redirect_to_another_domain_needs_evidence_on_the_landing_page():
    sites = {"gangstadbygg.no": site("https://www.unrelated-group.com/", title="Global Group plc", text="Gangstad Bygg " * 40)}
    found, _ = discover(PROFILE, "post@gangstadbygg.no", fetch=fake_fetch(sites), dns=lambda domain: domain == "gangstadbygg.no")
    assert found is None


def test_shared_chain_domain_is_not_an_exact_match():
    base = {"organisation_number": ORG, "website": "espira.no", "evidence": {"registry_live": {"value": {"website": "espira.no", "in_group": False}}, "website": {"value": {"final_url": "https://espira.no/", "title": "Velkommen til barnehagen", "main_text_excerpt": "Vi driver barnehager i hele landet. " * 6, "pages": []}}}}
    assert assess_website_identity({**base, "name": "ESPIRA SNURREFJELLET AS"})["publishable"] is False
    assert assess_website_identity({**base, "name": "ESPIRA HOLDING AS", "registered_domain_filers": 38})["publishable"] is False
    assert assess_website_identity({**base, "name": "ESPIRA HOLDING AS", "registered_domain_filers": 1})["publishable"] is True


def test_org_number_in_a_list_of_units_is_not_proof():
    listing = "Bunnpris Ski AS org.nr 998 877 665 Bunnpris Vest AS org.nr 912 345 678"
    assert find_org_number(listing, "912345678", ["kolbotn", "dagligvare"]) is None
    assert find_org_number("Kolbotn Dagligvare AS org.nr 912 345 678, Bunnpris Ski AS org.nr 998 877 665", "912345678", ["kolbotn"])
    assert find_org_number("Org.nr 912 345 678", "912345678", ["kolbotn"])


def test_social_handle_must_equal_the_name_or_the_site_domain():
    assert assess_social_identity({"name": "BERGEN ELEKTRO AS"}, {"url": "https://facebook.com/bergenkommune"}, "bergenelektro.no")["publishable"] is False
    assert assess_social_identity({"name": "HANSEN BYGG AS"}, {"url": "https://linkedin.com/company/hansen-webdesign"}, "hansenbygg.no")["publishable"] is False
    assert assess_social_identity({"name": "NOVOOI AS"}, {"url": "https://facebook.com/novooi.official"}, "novooi.com")["publishable"] is True
    assert assess_social_identity({"name": "RB PROFF PARTNER AS"}, {"url": "https://facebook.com/rbproffpartner"}, "rbproffpartner.no")["publishable"] is True


def test_job_path_does_not_match_ordinary_words():
    from norway_company_agent.website import JOB_PATH
    assert JOB_PATH.search("/forestillinger") is None and JOB_PATH.search("/utstillinger/2026") is None and JOB_PATH.search("/bestilling/kvittering") is None
    assert JOB_PATH.search("/ledige-stillinger") and JOB_PATH.search("/stillinger/") and JOB_PATH.search("/jobb-hos-oss") and JOB_PATH.search("/alt-om-oss/jobbe-hos-oss") and JOB_PATH.search("/om/jobb") and JOB_PATH.search("/careers")
    assert JOB_PATH.search("/skolen/hvordan-jobber-vi/") is None and JOB_PATH.search("/slik-jobber-vi") is None


def test_snapshot_without_the_company_still_gives_a_terminal_profile(tmp_path):
    from norway_company_agent.batch import profiles_from_bulk
    bulk = tmp_path / "bulk.csv"
    bulk.write_text('organisasjonsnummer,navn,hjemmeside\n"111111111","A AS","a.no"\n"222222222","B AS","a.no"\n', encoding="utf-8")
    profiles, meta = profiles_from_bulk(bulk, ["111111111", "333333333"])
    assert [p["organisation_number"] for p in profiles] == ["111111111", "333333333"]
    assert profiles[0]["registered_domain_filers"] == 2 and profiles[1]["evidence"]["registry"]["status"] == "not_found"
    assert meta["missing_from_snapshot"] == ["333333333"]


def test_wikidata_site_is_only_a_candidate():
    good = {"gangstadbygg.no": site("https://gangstadbygg.no/", org_found=True)}
    found, _ = discover(PROFILE, None, extra_sites=["https://www.gangstadbygg.no/om"], fetch=fake_fetch(good), dns=lambda domain: domain == "gangstadbygg.no")
    assert found["value"]["discovery"]["origin"] == "wikidata_official_website"
    unrelated = {"coop.no": site("https://coop.no/", title="Coop", text="Butikker over hele landet. " * 20)}
    found, metrics = discover(PROFILE, None, extra_sites=["https://www.coop.no/x"], fetch=fake_fetch(unrelated), dns=lambda domain: domain == "coop.no")
    assert found is None and metrics["candidates"][0]["outcome"] == "rejected_identity"


def _site_profile():
    url = "https://gangstadbygg.no/"
    value = {"final_url": url, "title": "Gangstad Bygg AS", "description": "", "main_text_excerpt": "x" * 200, "social_links": [], "structured_organisations": [{"name": "Gangstad"}], "org_number_found_at": url + "kontakt",
             "pages": [{"url": url, "title": "Gangstad Bygg AS"}, {"url": url + "kontakt", "title": "Kontakt", "org_number_excerpt": "Org.nr 985 589 003"}],
             "identity_assessment": {"publishable": True, "score": 1.0, "reasons": ["x"], "method": "m"}}
    group = {"status": "available", "source_url": "https://g", "value": {"children": [{"organisasjonsnummer": "111111111", "navn": "Datter AS", "knytningsform": {"beskrivelse": "Konsern datter", "kode": "KDAT"}, "dato": "2020-01-01"}, {"organisasjonsnummer": ORG, "navn": "Self"}]}}
    return {"organisation_number": ORG, "name": "GANGSTAD BYGG AS", "evidence": {"website": {"status": "available", "source_url": url, "retrieved_at": "t", "value": value}, "group": group}}


def test_website_claim_cites_the_page_that_proved_it():
    claims, rows, _ = build_claims(_site_profile())
    site_claim = next(c for c in claims if c["field"] == "official_website")
    proof = next(r for r in rows if r["id"] == site_claim["evidence_ids"][1])
    assert proof["source_url"].endswith("/kontakt") and "985 589 003" in proof["claim_span"]
    assert all(row.get("extraction_method") for row in rows)


def test_public_brand_and_group_links_are_published():
    claims, _, _ = build_claims(_site_profile())
    assert next(c for c in claims if c["field"] == "public_brand")["value"] == "Gangstad"
    links = [c for c in claims if c["field"] == "group.link"]
    assert len(links) == 1 and links[0]["value"]["relationship_code"] == "KDAT"


def test_sitemap_items_cite_the_child_file_and_prefer_a_date_in_the_url(monkeypatch):
    import norway_company_agent.website as website_module
    pages = {
        "https://a.no/sitemap.xml": ("<sitemapindex><sitemap><loc>https://a.no/post-sitemap.xml</loc></sitemap></sitemapindex>", "root-hash"),
        "https://a.no/post-sitemap.xml": ("<urlset><url><loc>https://a.no/2018/08/16/kraftverk/</loc><lastmod>2024-10-17</lastmod></url></urlset>", "child-hash"),
    }
    monkeypatch.setattr(website_module, "_fetch_text", lambda url, timeout, max_bytes=1000000: pages.get(url, ("", None)))
    result = website_module.sitemap_items("https://a.no/")
    assert result["news"][0]["source_url"] == "https://a.no/post-sitemap.xml" and result["news"][0]["date"] == "2018-08-16" and result["news"][0]["date_basis"] == "date_in_url"
    assert {"url": "https://a.no/post-sitemap.xml", "sha256": "child-hash"} in result["documents"]


def test_decode_html_honours_latin1():
    from norway_company_agent.website import _decode_html
    html = '<html><head><meta charset="iso-8859-1"><title>Bj\xf8rn Bygg AS</title></head></html>'.encode("latin-1")
    assert "Bjørn Bygg AS" in _decode_html(html)


def test_sitemap_with_unclosed_url_tags_stays_linear(monkeypatch):
    import time as _time
    import norway_company_agent.website as website_module
    body = "<urlset>" + "<url><loc>https://a.no/x</loc>" * 8000 + "</urlset>"
    monkeypatch.setattr(website_module, "_fetch_text", lambda url, timeout, max_bytes=1000000: (body, "h") if url.endswith("/sitemap.xml") else ("", None))
    started = _time.monotonic()
    website_module.sitemap_items("https://a.no/")
    assert _time.monotonic() - started < 3


def test_write_jsonl_survives_a_lone_surrogate(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("batch_cli", Path(__file__).parents[1] / "scripts" / "run_competition_batch.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "out.jsonl"
    module.write_jsonl(target, [{"title": "a\ud83db"}])
    assert target.read_text(encoding="utf-8").strip()


def test_rejected_site_stays_out_of_the_envelope_profile():
    from norway_company_agent.batch import _envelope_profile
    website = {"status": "available", "retrieved_at": "t", "value": {"final_url": "https://7-eleven.no/", "title": "7-Eleven", "identity_assessment": {"publishable": False}}}
    view = _envelope_profile({"organisation_number": ORG, "site_discovery": {"candidates": [{"domain": "7-eleven.no"}]}, "evidence": {"website": website}})
    assert view["evidence"]["website"]["status"] == "ambiguous" and "value" not in view["evidence"]["website"] and "site_discovery" not in view


def _fake_response(body: bytes, url: str, content_type: str = "text/plain"):
    class Response:
        headers = {"content-type": content_type}

        def __init__(self):
            self.remaining = [body]

        def geturl(self):
            return url

        def read1(self, _amount):
            return self.remaining.pop(0) if self.remaining else b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False
    return Response()


def test_robots_redirect_to_another_host_uses_that_hosts_rules(monkeypatch):
    import norway_company_agent.website as website_module
    answers = {
        "https://a.no/robots.txt": _fake_response(b"<html>home</html>", "http://www.a.no/", "text/html"),
        "http://www.a.no/robots.txt": _fake_response(b"User-agent: *\nDisallow: /\n", "http://www.a.no/robots.txt"),
    }
    monkeypatch.setattr(website_module, "assert_public_url", lambda url: None)
    monkeypatch.setattr(website_module.SAFE_OPENER, "open", lambda request, timeout=None: answers[request.full_url])
    assert website_module._robots_allowed("https://a.no/", 5.0, if_unreachable=True) is False


def test_html_answer_for_robots_on_the_same_host_is_no_robots_file(monkeypatch):
    import norway_company_agent.website as website_module
    monkeypatch.setattr(website_module, "assert_public_url", lambda url: None)
    monkeypatch.setattr(website_module.SAFE_OPENER, "open", lambda request, timeout=None: _fake_response(b"<html>home</html>", "https://a.no/", "text/html"))
    assert website_module._robots_allowed("https://a.no/x", 5.0) is True


def test_feed_items_reads_rss_and_atom_with_real_titles(monkeypatch):
    import norway_company_agent.website as website_module
    from bs4 import BeautifulSoup
    rss = '<rss><channel><item><title><![CDATA[Fasadeendring p\u00e5 Bryggen]]></title><link>https://a.no/fasade/</link><pubDate>Thu, 16 Aug 2018 10:00:00 +0000</pubDate></item><item><title>Elsewhere</title><link>https://other.no/x</link><pubDate>Thu, 16 Aug 2018 10:00:00 +0000</pubDate></item></channel></rss>'
    monkeypatch.setattr(website_module, "_fetch_text", lambda url, timeout, max_bytes=1000000: (rss, "h"))
    soup = BeautifulSoup('<link rel="alternate" type="application/rss+xml" href="/feed/">', "lxml")
    result = website_module.feed_items(soup, "https://a.no/")
    assert result["items"] == [{"title": "Fasadeendring p\u00e5 Bryggen", "url": "https://a.no/fasade/", "date": "2018-08-16", "source_url": "https://a.no/feed/", "date_basis": "feed_publication_date"}]
    atom = '<feed><entry><title>Ny butikk</title><link rel="alternate" href="https://a.no/ny/"/><published>2026-03-01T08:00:00Z</published></entry></feed>'
    monkeypatch.setattr(website_module, "_fetch_text", lambda url, timeout, max_bytes=1000000: (atom, "h"))
    assert website_module.feed_items(soup, "https://a.no/")["items"][0]["date"] == "2026-03-01"


def test_job_board_links_come_only_from_known_boards():
    from bs4 import BeautifulSoup
    from norway_company_agent.website import job_board_links
    html = '<a href="https://www.finn.no/job/ad/442503773">Konsulent</a><a href="https://nilfisk.wd3.myworkdayjobs.com/Nilfisk">Jobs</a><a href="https://www.finn.no/">FINN</a><a href="https://facebook.com/x">FB</a><a href="http://[bad">x</a>'
    links = job_board_links(BeautifulSoup(html, "lxml"), "https://a.no/karriere")
    assert [link["board_host"] for link in links] == ["www.finn.no", "nilfisk.wd3.myworkdayjobs.com"] and links[0]["anchor_text"] == "Konsulent"


def _available(field, value, **extra):
    return {"field": field, "value": value, "availability": "available", "evidence_ids": ["ev-x"], **extra}


def test_refresh_does_not_report_a_failed_module_as_removed():
    from norway_company_agent.changes import diff
    period = {"fraDato": "2024-01-01", "tilDato": "2024-12-31"}
    previous = [_available("financials.revenue", 100.0, reporting_period=period), _available("official_website", "https://a.no/"), _available("company_profile.facebook", "https://facebook.com/a")]
    current = [{"field": "financials", "value": None, "availability": "failed", "evidence_ids": []}, {"field": "official_website", "value": None, "availability": "failed", "evidence_ids": []}]
    assert diff(current, previous) == []


def test_refresh_is_silent_when_a_source_comes_back_and_when_a_year_rolls_off():
    from norway_company_agent.changes import diff
    old_period = {"fraDato": "2022-01-01", "tilDato": "2022-12-31"}
    new_period = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
    previous = [{"field": "official_website", "value": None, "availability": "failed", "evidence_ids": []}, _available("financials.revenue", 1.0, reporting_period=old_period)]
    current = [_available("official_website", "https://a.no/"), _available("financials.revenue", 2.0, reporting_period=new_period)]
    changes = diff(current, previous)
    assert [c["field"] for c in changes] == ["financials.revenue"] and changes[0]["type"] == "added"


def test_refresh_keeps_account_types_apart():
    from norway_company_agent.changes import diff
    period = {"fraDato": "2025-01-01", "tilDato": "2025-12-31"}
    previous = [_available("financials.revenue", 100.0, reporting_period=period, account_type="SELSKAP"), _available("financials.revenue", 900.0, reporting_period=period, account_type="KONSERN")]
    current = [_available("financials.revenue", 150.0, reporting_period=period, account_type="SELSKAP"), _available("financials.revenue", 900.0, reporting_period=period, account_type="KONSERN")]
    assert [c["type"] for c in diff(current, previous)] == ["changed"]


def test_prior_evidence_is_kept_on_an_unchanged_refresh():
    from norway_company_agent.changes import apply_previous
    evidence_row = {"id": "ev-registry", "source_url": "https://x", "content_sha256": "h"}
    envelope = {"claims": [], "evidence": []}
    apply_previous(envelope, {"run_id": "r1", "claims": [], "evidence": [evidence_row]})
    assert envelope["prior_evidence"] and envelope["prior_evidence"][0]["id"] == "ev-registry@r1"


def _money_claim(field, value, end, account="SELSKAP"):
    return {"field": field, "value": value, "availability": "available", "currency": "NOK", "account_type": account, "reporting_period": {"fraDato": end[:4] + "-01-01", "tilDato": end}}


class Drip:
    def __init__(self, parts):
        self.parts = list(parts)

    def read1(self, _amount):
        return self.parts.pop(0) if self.parts else b""


def test_read_capped_stops_at_the_limit_and_at_end_of_body():
    assert _read_capped(Drip([b"abc", b"def"]), 4) == b"abcdef"[:6]
    assert _read_capped(Drip([b"ab", b"cd"]), 100) == b"abcd"


def test_read_capped_gives_up_on_a_slow_drip():
    class Slow:
        def read1(self, _amount):
            import time as _time
            _time.sleep(0.02)
            return b"x"
    try:
        _read_capped(Slow(), 10_000, deadline_seconds=0.05)
    except TimeoutError:
        return
    raise AssertionError("expected a timeout")


def address_site(url: str, title: str) -> dict:
    site_value = site(url, title=title, text="Vi lager apper. " * 20)
    site_value["value"]["registered_address_found_at"] = url + "kontakt"
    site_value["value"]["registered_address_excerpt"] = "Bjornstadveien 16, 1712 Gralum"
    return site_value


def test_name_domain_with_registered_address_is_accepted_even_after_a_redirect():
    found, _ = discover(PROFILE, None, fetch=fake_fetch({"gangstadbygg.no": address_site("https://www.studiox.no/", "Studio X")}), dns=lambda domain: domain == "gangstadbygg.no")
    assert found is not None
    reasons = found["value"]["identity_assessment"]["reasons"]
    assert any("equals the legal name" in reason for reason in reasons) and any("redirects to this site" in reason for reason in reasons)
    assert found["value"]["discovery"]["candidate"] == "gangstadbygg.no"


def test_other_domain_with_registered_address_stays_rejected():
    found, _ = discover(PROFILE, None, fetch=fake_fetch({"gangstad.no": address_site("https://gangstad.no/", "Annet Firma")}), dns=lambda domain: domain == "gangstad.no")
    assert found is None


def test_accepted_site_reports_the_real_basis_not_the_gate_text():
    sites = {"gangstadbygg.no": site("https://gangstadbygg.no/", title="Velkommen", text="Gangstad Bygg bygger hus. " * 20)}
    found, _ = discover(PROFILE, "post@gangstadbygg.no", fetch=fake_fetch(sites), dns=lambda domain: domain == "gangstadbygg.no")
    reasons = found["value"]["identity_assessment"]["reasons"]
    assert reasons == ["the domain gangstadbygg.no equals the legal name"]


def test_summary_uses_the_latest_year_whatever_the_claim_order():
    from norway_company_agent.claims import _financial_sentences
    claims = [_money_claim("financials.revenue", 300.0, "2023-12-31"), _money_claim("financials.revenue", 200.0, "2024-12-31"), _money_claim("financials.revenue", 100.0, "2025-12-31"), _money_claim("financials.annual_result", 7.0, "2025-12-31")]
    sentences = _financial_sentences(claims)
    assert "2025-12-31 was 100 NOK, with an annual result of 7 NOK" in sentences[0]
    assert "200 NOK in the period ending 2024-12-31" in sentences[1] and "(-50.0%)" in sentences[1]
    assert _financial_sentences(list(reversed(claims))) == sentences


def test_summary_does_not_compare_across_a_filing_gap_or_a_zero_base():
    from norway_company_agent.claims import _financial_sentences
    gap = [_money_claim("financials.revenue", 100.0, "2025-12-31"), _money_claim("financials.revenue", 50.0, "2022-12-31")]
    assert len(_financial_sentences(gap)) == 1
    zero = [_money_claim("financials.revenue", 100.0, "2025-12-31"), _money_claim("financials.revenue", 0.0, "2024-12-31")]
    assert "%" not in _financial_sentences(zero)[1]


def test_summary_without_revenue_reports_the_result_and_says_revenue_is_absent():
    from norway_company_agent.claims import _financial_sentences
    sentences = _financial_sentences([_money_claim("financials.annual_result", -35903.0, "2025-12-31")])
    assert "no revenue figure was filed" in sentences[0] and "-35,903 NOK" in sentences[0]
    assert _financial_sentences([]) == []


def test_name_candidates_keep_the_order_and_add_wider_guesses_last():
    names = name_candidates("Bjørn Hansen Transport AS")
    assert names[0] == "bjornhansentransport.no" and names[1] == "bjornhansentransport.com"
    assert "bjoernhansentransport.no" in names and "hansentransport.no" in names
    assert names.index("bjornhansen.no") < names.index("hansentransport.no")
    assert len(names) <= 14


def test_phone_match_ignores_spacing_and_refuses_a_longer_digit_run():
    hints = {"phones": ["+47 22 33 44 55"]}
    assert find_phone("Ring oss på 22 33 44 55 i dag", hints)
    assert find_phone("Tlf +47 223 34 455", hints)
    assert find_phone("Kontonummer 1222334455 00", hints) is None
    assert find_phone("Ring 22 33 44 56", hints) is None


def phone_site(title: str, url: str = "https://gangstadbygg.no/") -> dict:
    site_value = site(url, title=title, text="Vi bygger hus. " * 20)
    site_value["value"]["registered_phone_found_at"] = url + "kontakt"
    site_value["value"]["registered_phone_excerpt"] = "Tlf 22 33 44 55"
    return site_value


def test_guess_with_registered_phone_needs_all_name_tokens():
    found, _ = discover(PROFILE, None, fetch=fake_fetch({"gangstadbygg.no": phone_site("Gangstad Bygg AS")}), dns=lambda domain: domain == "gangstadbygg.no")
    assert found is not None and found["value"]["discovery"]["registered_phone_on_site"] is True
    found, _ = discover(PROFILE, None, fetch=fake_fetch({"gangstad.no": phone_site("Annet Firma AS", "https://gangstad.no/")}), dns=lambda domain: domain == "gangstad.no")
    assert found is None


def test_registered_phone_on_a_domain_equal_to_the_legal_name_is_accepted():
    found, _ = discover(PROFILE, None, fetch=fake_fetch({"gangstadbygg.no": phone_site("Annet Firma AS")}), dns=lambda domain: domain == "gangstadbygg.no")
    assert found is not None


def test_group_member_does_not_get_the_registry_domain_anchor():
    profile = {"organisation_number": ORG, "name": "BEKK HOLDING AS", "website": "www.bekk.no", "evidence": {"registry_live": {"value": {"website": "www.bekk.no", "in_group": True}}, "website": {"value": {"final_url": "https://www.bekk.no/", "title": "Bekk", "main_text_excerpt": "Bekk lager digitale tjenester. " * 5, "pages": []}}}}
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
    assert all(c["value"] is None and c["availability"] == "ambiguous" for c in claims if c["field"] == "self_description")


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
    assert same["changes"] == [] and same["prior_evidence"] == [{"id": "ev-1@r1"}]
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


def _load_site_builder():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site", Path(__file__).resolve().parents[1] / "scripts" / "build_site.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _site_envelope(name: str = "TEST AS") -> dict:
    return {
        "organisation_number": ORG, "run_id": "r1", "completed_at": "2026-11-09T10:00:00Z", "profile": {"name": name}, "summary": {"text": "s"}, "changes": [], "refresh": {"first_observation": True},
        "evidence": [{"id": "e1", "source_url": "https://data.brreg.no/x", "source_class": "official_roles", "retrieved_at": "2026-11-09T10:00:00Z", "content_sha256": "ab", "extraction_method": "brreg_api_json"}, {"id": "unused", "source_url": "https://example.no/unused"}],
        "claims": [
            {"field": "leadership.role", "availability": "available", "value": {"name": ["ATELLA REGNSKAP AS"], "role": "Regnskapsfoerer", "role_code": "REGN"}, "evidence_ids": ["e1"]},
            {"field": "financials.revenue", "availability": "not_available", "value": None, "evidence_ids": ["e1"]},
            {"field": "business_address", "availability": "available", "value": {"adresse": ["Gate 1"], "postnummer": "0001", "poststed": "OSLO"}, "evidence_ids": ["e1"]},
        ],
    }


def test_site_row_formats_values_and_keeps_missing_missing() -> None:
    builder = _load_site_builder()
    row = builder.row(_site_envelope())
    by_field = {claim["f"]: claim for claim in row["claims"]}
    assert by_field["leadership.role"]["v"] == "ATELLA REGNSKAP AS"
    assert by_field["business_address"]["v"] == "Gate 1, 0001 OSLO"
    assert "n" not in by_field["financials.revenue"] and by_field["financials.revenue"]["v"] == ""  # a missing figure must never become 0
    assert set(row["ev"]) == {"e1"}  # only evidence a claim cites is shipped


def test_site_page_cannot_be_broken_out_of_by_data() -> None:
    builder = _load_site_builder()
    page, count = builder.render([_site_envelope("</script><img src=x onerror=alert(1)>")])
    assert count == 1
    assert "</script><img" not in page
    assert page.count("</script>") == 1


def test_site_build_writes_one_page(tmp_path) -> None:
    import json as json_module
    builder = _load_site_builder()
    source = tmp_path / "e.jsonl"
    source.write_text(json_module.dumps(_site_envelope()) + "\n\n", encoding="utf-8")
    assert builder.build(source, tmp_path / "site" / "index.html") == 1
    assert 'content="noindex"' in (tmp_path / "site" / "index.html").read_text(encoding="utf-8")


def test_stories_and_policies_on_a_careers_page_are_not_jobs() -> None:
    from norway_company_agent.website import _is_job_link
    for title, path in [
        ("Nilfisk's data privacy policy", "/om/job-and-career/privacy"),
        ("Vil du jobbe som vikar hos oss?", "/alt-om-oss/jobbe-hos-oss/vikar"),
        ("Sommerjobb i Sykehuset: Erfaringer for livet", "/jobbe-hos-oss/sommerjobb"),
        ("Bli bedre kjent med noen av våre medarbeidere", "/jobbe-hos-oss/derfor"),
        ("nye stillinger som trainee er lyst ut", "/nyheter/nye-stillinger"),
        ("Kultur og velferd", "/jobbe-hos-oss/kultur-og-velferd"),
    ]:
        assert not _is_job_link(title, path), title


def test_real_postings_stay_jobs() -> None:
    from norway_company_agent.website import _is_job_link
    for title, path in [
        ("Prosjektleder maskinteknikk", "/karriere/prosjektleder"),
        ("cnc operator", "/careers/vacancies/cnc-operator/"),
        ("summer internship 2027", "/careers/vacancies/summer-internship-2027/"),
    ]:
        assert _is_job_link(title, path), title


def test_xhtml_page_keeps_its_structured_data() -> None:
    import extruct
    from norway_company_agent.website import _no_xml_declaration
    page = '<?xml version="1.0" encoding="UTF-8"?><html><body><script type="application/ld+json">{"@type":"Organization","name":"A"}</script></body></html>'
    assert extruct.extract(_no_xml_declaration(page), base_url="https://a.no/", syntaxes=["json-ld"], errors="log")["json-ld"][0]["name"] == "A"


def test_site_describes_a_change_instead_of_only_naming_the_field() -> None:
    builder = _load_site_builder()
    envelope = _site_envelope()
    envelope["changes"] = [{"type": "changed", "field": "financials.revenue", "previous": 100.0, "current": 200.0}, {"type": "removed", "field": "official_website", "previous": "https://a.no/"}]
    assert builder.row(envelope)["changes"] == ["Changed: financials revenue: 100 \u2192 200", "No longer found: official website: https://a.no/"]


def _profile_with_leader(name: str = "Kari Fjellheim", code: str = "DAGL", organisation: str | None = None) -> dict:
    return {"organisation_number": ORG, "name": "GANGSTAD BYGG AS", "evidence": {"roles": {"value": {"roles": [{"name": name, "role_code": code, "organisation_number": organisation, "inactive": False}]}}}}


def test_leader_named_on_site_counts_only_in_full_and_with_the_right_role() -> None:
    from norway_company_agent.discover_site import _leader_on_site
    value = {"title": "Kontakt", "main_text_excerpt": "Daglig leder: Kari Marie Fjellheim, telefon", "pages": [{"main_text_excerpt": "Vår leder heter Fjellheimsen"}]}
    assert _leader_on_site(_profile_with_leader("Kari Marie Fjellheim"), value)
    assert _leader_on_site(_profile_with_leader("Kari Marie Fjellheim"), {"main_text_excerpt": "kontakt Kari Fjellheim i dag"})
    assert not _leader_on_site(_profile_with_leader("Kari Marie Fjellheim"), {"main_text_excerpt": "Fjellheim er en fin plass, spor Kari"})
    assert not _leader_on_site(_profile_with_leader("Kari Fjellheim", code="REVI"), value)  # an auditor is not the company's own leader
    assert not _leader_on_site(_profile_with_leader("Kari Fjellheim", organisation="123456789"), value)  # role held by a company, not a person
    assert not _leader_on_site(_profile_with_leader("Ole Li"), {"main_text_excerpt": "Ole Li"})  # too short to prove anything
    assert not _leader_on_site(_profile_with_leader("Kari Fjell"), {"main_text_excerpt": "Kari Fjellheim"})  # a name inside a longer word is not a match


def test_summary_uses_the_companys_own_latest_accounts_and_labels_group_figures() -> None:
    from norway_company_agent.claims import _financial_sentences

    def fact(field: str, value: float, end: str, kind: str) -> dict:
        return {"field": field, "value": value, "availability": "available", "currency": "NOK", "reporting_period": {"fraDato": end[:4] + "-01-01", "tilDato": end}, "account_type": kind}

    mixed = [fact("financials.revenue", 900.0, "2023-12-31", "KONSERN"), fact("financials.annual_result", -5.0, "2025-12-31", "SELSKAP")]
    assert _financial_sentences(mixed)[0].startswith("The filed annual result for the period ending 2025-12-31 was -5 NOK")
    assert _financial_sentences([fact("financials.revenue", 900.0, "2023-12-31", "KONSERN")])[0].startswith("Filed group revenue for the period ending 2023-12-31")


def test_holding_company_needs_the_exact_organisation_number() -> None:
    def fetched(domain: str) -> tuple[dict, dict]:
        return {"status": "available", "value": {"final_url": "https://cosma.no/", "title": "Cosma", "description": "", "main_text_excerpt": "Cosma hjelper virksomheter med HR. " * 10, "pages": [], "social_links": [], "registered_address_found_at": "https://cosma.no/kontakt"}}, {"requests": 1, "bytes": 10, "latencies_ms": [5]}

    profile = {"organisation_number": ORG, "name": "COSMA HOLDING AS", "evidence": {}}
    found, metrics = discover(profile, None, fetch=fetched, dns=lambda domain: domain == "cosma.no")
    assert found is None
    assert any(item.get("outcome") == "rejected_identity" for item in metrics["candidates"])


def test_only_a_single_dated_article_page_counts_as_activity() -> None:
    from norway_company_agent.claims import _is_article_page, _is_product_code
    assert _is_article_page({"url": "https://a.no/nyheter/ny-avtale", "stated_dates": ["2026-01-02"]})
    assert not _is_article_page({"url": "https://a.no/blogg/", "stated_dates": ["2026-01-02"]})  # top-level section page
    assert not _is_article_page({"url": "https://a.no/blogs/nyheter", "stated_dates": ["2026-01-02", "2026-02-02"]})  # a listing: the dates belong to its items
    assert _is_product_code("750P201T127") and not _is_product_code("Design Dialogues") and not _is_product_code("Nyhet 2026")


def test_a_description_that_only_repeats_the_name_is_not_published() -> None:
    from norway_company_agent.claims import _only_repeats_name
    assert _only_repeats_name("Skjetten Legesenter", "SKJETTEN LEGESENTER AS")
    assert _only_repeats_name("CreationFlow", "CREATIONFLOW AS")
    assert not _only_repeats_name("Skjetten Legesenter tilbyr fastlege", "SKJETTEN LEGESENTER AS")


def test_redirect_to_a_sister_company_is_not_this_companys_site() -> None:
    def fetched(domain: str) -> tuple[dict, dict]:
        return {"status": "available", "value": {"final_url": "https://www.vingruppen.no/strom", "title": "Vingruppen | Strom AS", "description": "", "main_text_excerpt": "Vinhandler siden 1921. Strom AS har importert vin. " * 6, "pages": [], "social_links": [], "registered_address_found_at": "https://www.vingruppen.no/strom"}}, {"requests": 1, "bytes": 10, "latencies_ms": [5]}

    profile = {"organisation_number": ORG, "name": "PREMIUM WINES AS", "evidence": {}}
    found, _ = discover(profile, None, fetch=fetched, dns=lambda domain: domain == "premiumwines.no")
    assert found is None


def test_redirect_with_only_a_leader_name_is_not_enough() -> None:
    def fetched(domain: str) -> tuple[dict, dict]:
        return {"status": "available", "value": {"final_url": "https://www.annetfirma.no/", "title": "Hjem", "description": "", "main_text_excerpt": "Daglig leder Kari Fjellheim driver emballasje. " * 6, "pages": [], "social_links": []}}, {"requests": 1, "bytes": 10, "latencies_ms": [5]}

    found, _ = discover(_profile_with_leader(), None, fetch=fetched, dns=lambda domain: domain == "gangstadbygg.no")
    assert found is None
