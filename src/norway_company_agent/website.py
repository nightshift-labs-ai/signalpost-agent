from __future__ import annotations

import html as html_module
import json
import ipaddress
import re
import socket
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup, UnicodeDammit
import extruct
import tldextract
import trafilatura

from .evidence import evidence

USER_AGENT = "nightshift-signalpost/1.0 (+https://github.com/nightshift-labs-ai)"
SOCIAL_HOSTS = {
    "linkedin.com": "linkedin",
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "x.com": "x",
    "twitter.com": "x",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "tiktok.com": "tiktok",
}
PRIORITY_TERMS = (
    "om-oss", "om_oss", "about", "kontakt", "contact", "ledelse", "management",
    "team", "people", "locations", "lokasjoner", "avdelinger", "butikker",
    "news", "press", "aktuelt", "nyheter", "artikler", "blogg", "blog",
    "karriere", "career", "/jobb", "jobbe-hos", "ledige-stillinger", "/stillinger", "vacancies",
)
MAX_SECONDARY_PAGES = 8
READ_DEADLINE_SECONDS = 20.0
SITE_BUDGET_SECONDS = 45.0
ORG_NUMBER_PATTERN = re.compile(r"(?<!\d)(\d{3})[\s\u00a0.]?(\d{3})[\s\u00a0.]?(\d{3})(?!\d)")
JOB_PATH = re.compile(r"karriere|career|vacanc|(?:^|[/_-])(?:ledige-)?stillinger?(?:[/_.-]|$)|(?:^|[/_-])jobb?s?(?:[/_.-]|$)|jobbe?-(?:hos|i|med|for)\b", re.I)
NEWS_PATH = re.compile(r"/(?:news|press|aktuelt|nyheter|nyhet|artikler|artikkel|blogg?|blogs)(?:/|$)", re.I)


def _read_capped(response: Any, limit: int, deadline_seconds: float = READ_DEADLINE_SECONDS) -> bytes:
    """Read at most limit bytes within a wall-clock deadline.

    The socket timeout only limits each single read, so a server trickling a byte at a time could hold a worker for many minutes.
    """
    read_some = getattr(response, "read1", None) or response.read
    deadline = time.monotonic() + deadline_seconds
    chunks: list[bytes] = []
    total = 0
    while total < limit:
        chunk = read_some(min(65536, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if time.monotonic() > deadline:
            raise TimeoutError("page read exceeded the wall-clock limit")
    return b"".join(chunks)


_TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)  # the bundled list: no undeclared network call, same result on every machine


def _decode_html(raw: bytes) -> str:
    """Honour the page's real encoding; older Norwegian sites are often Latin-1."""
    return UnicodeDammit(raw, is_html=True).unicode_markup or raw.decode("utf-8", errors="replace")


def find_org_number(text: str, org: str | None, name_tokens: list[str] | None = None) -> str | None:
    """Excerpt around the exact organisation number, or None.

    A page listing several organisation numbers (a chain's store list, a customer list) proves nothing
    about who owns the site, so there the excerpt must also carry a word from the company's name.
    """
    if not org:
        return None
    matches = list(ORG_NUMBER_PATTERN.finditer(text))
    several = len({"".join(match.groups()) for match in matches}) > 1
    for match in matches:
        if "".join(match.groups()) != org:
            continue
        excerpt = " ".join(text[max(0, match.start() - 80):match.end() + 40].split())
        if several and name_tokens and not any(token in _fold(excerpt) for token in name_tokens):
            continue
        return excerpt
    return None


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.translate(str.maketrans({"ø": "o", "Ø": "o", "å": "a", "Å": "a", "æ": "ae", "Æ": "ae"}))).encode("ascii", "ignore").decode().casefold()


def find_address(text: str, hints: dict[str, Any] | None) -> str | None:
    """Excerpt where the registered postcode and street name occur close together, or None."""
    postcode = str((hints or {}).get("postcode") or "")
    street = str((hints or {}).get("street") or "").casefold()
    if not re.fullmatch(r"\d{4}", postcode) or len(street) < 4:
        return None
    lowered = text.casefold()
    for match in re.finditer(r"(?<!\d)" + postcode + r"(?!\d)", lowered):
        window = lowered[max(0, match.start() - 120):match.end() + 40]
        if street in window:
            return " ".join(text[max(0, match.start() - 80):match.end() + 40].split())
    return None


def find_phone(text: str, hints: dict[str, Any] | None) -> str | None:
    """Excerpt where one of the company's registered phone numbers appears (any spacing), or None."""
    for raw in (hints or {}).get("phones") or []:
        digits = re.sub(r"\D", "", str(raw or ""))[-8:]
        if len(digits) != 8:
            continue
        match = re.search(r"(?<!\d)" + r"[\s.\-]{0,2}".join(digits) + r"(?!\d)", text)
        if match:
            return " ".join(text[max(0, match.start() - 60):match.end() + 30].split())
    return None


def _page_dates(html_metadata: dict[str, Any], soup: BeautifulSoup) -> list[str]:
    """Publication dates the page states about itself; never inferred from the crawl time."""
    found: list[str] = []
    for tag in soup.select('meta[property="article:published_time"], meta[name="date"], meta[name="pubdate"]'):
        found.append(str(tag.get("content") or ""))
    for tag in soup.select("article time[datetime], main time[datetime]")[:10]:
        found.append(str(tag.get("datetime") or ""))

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("datePublished"), str):
                found.append(node["datePublished"])
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(html_metadata.get("json-ld", []))
    return sorted({item[:10] for item in found if re.match(r"^\d{4}-\d{2}-\d{2}", item)})


JOB_BOARD_LINK = re.compile(r"^(?:[a-z0-9-]+\.)*(?:myworkdayjobs\.com/|teamtailor\.com/|webcruiter\.(?:no|com)/|jobylon\.com/|jobbnorge\.no/|reachmee\.com/|recman\.no/|recruitee\.com/|varbi\.com/|hrmanager\.com/|cvideo\.no/|finn\.no/job/(?:ad|company|fulltime)|arbeidsplassen\.nav\.no/stillinger/stilling/)", re.I)


def job_board_links(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Recruitment-board links on the company's own careers page. The destination is reported, never fetched."""
    found: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("a[href]"):
        try:
            url = urllib.parse.urljoin(page_url, str(anchor.get("href") or "").strip())
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or not JOB_BOARD_LINK.match(parsed.netloc.lower() + parsed.path):
            continue
        found[url] = {"url": url[:300], "board_host": parsed.netloc.lower(), "anchor_text": " ".join(anchor.get_text(" ", strip=True).split())[:200], "source_url": page_url}
    return list(found.values())[:20]


def listed_jobs(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Job links on a careers page: deeper same-host paths with a real title, not navigation."""
    base = urllib.parse.urlparse(page_url)
    base_depth = len([part for part in base.path.split("/") if part])
    found: dict[str, dict[str, Any]] = {}
    for anchor in soup.select("main a[href], article a[href], section a[href]"):
        url = urllib.parse.urljoin(page_url, str(anchor.get("href") or "").strip())
        parsed = urllib.parse.urlparse(url)
        title = " ".join(anchor.get_text(" ", strip=True).split())
        depth = len([part for part in parsed.path.split("/") if part])
        if parsed.netloc != base.netloc or depth <= base_depth or not JOB_PATH.search(parsed.path):
            continue
        if len(title.split()) < 2 or len(title) > 200 or re.search(r"se alle|ledige stillinger|les mer|søk her", title, re.I):
            continue
        if not _is_job_link(title, parsed.path):
            continue
        found[url.split("#")[0]] = {"title": title, "date_posted": None, "valid_through": None, "source_url": page_url, "posting_url": url.split("#")[0]}
    return list(found.values())[:30]


JOB_TITLE_WORD = re.compile(r"(?:leder|sjef|ingeni\u00f8r|konsulent|r\u00e5dgiver|medarbeider|operat\u00f8r|tekniker|mont\u00f8r|arbeider|sykepleier|l\u00e6rer|selger|utvikler|assistent|ansvarlig|koordinator|maskinf\u00f8rer|snekker|t\u00f8mrer|elektriker|r\u00f8rlegger|sj\u00e5f\u00f8r|kokk|regnskapsf\u00f8rer|revisor|advokat|kontroll\u00f8r|planlegger|kalkulat\u00f8r|mekaniker|sveiser|manager|engineer|developer|specialist|technician|consultant|operator|analyst|director|officer|coordinator|designer|accountant|trainee|l\u00e6rling)(?:er|e|en)?\b", re.I)
NOT_A_TITLE_START = re.compile(r"^(?:vi|du|ingen|for|send|kontakt|se|les|s\u00f8k|v\u00e5r|v\u00e5re|bli|hvordan|hva|hvorfor|our|we|no|view|apply|about|join|how|why|what)\b", re.I)


NOT_A_TITLE_WORD = re.compile(r"\b(?:som|av|din|deg|dine|v\u00e5re|privacy|policy|personvern)\b", re.I)
JOB_ITEM_PATH = re.compile(r"(?:^|/)(?:ledige-stillinger|stillinger?|vacanc(?:y|ies)|jobs?|jobb|openings?|positions?)/[^/]+", re.I)


def _looks_like_job_title(text: str) -> bool:
    words = text.split()
    return 1 <= len(words) <= 8 and len(text) <= 90 and text[-1] not in ".!?:;," and not NOT_A_TITLE_START.match(text) and not NOT_A_TITLE_WORD.search(text) and bool(JOB_TITLE_WORD.search(text))


def _is_job_link(title: str, path: str) -> bool:
    """A careers section also links stories, benefits and policies. A link counts as a job only when its title reads like one,
    or its own path is an item under a jobs listing and the title is not a sentence, question or policy page."""
    if _looks_like_job_title(title):
        return True
    plain = bool(title) and title[-1] not in ".!?:;," and ":" not in title and not NOT_A_TITLE_START.match(title) and not NOT_A_TITLE_WORD.search(title)
    return plain and bool(JOB_ITEM_PATH.search(path))


def news_items(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    """Dated items on a news listing: a block holding both a machine-readable date and a same-host link."""
    host = urllib.parse.urlparse(page_url).netloc
    found: dict[str, dict[str, Any]] = {}
    for time_tag in soup.select("time[datetime]")[:60]:
        date = str(time_tag.get("datetime") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            continue
        block = time_tag
        anchor = None
        for _ in range(4):
            block = block.parent
            if block is None:
                break
            anchor = block.select_one("a[href]")
            if anchor:
                break
        if not anchor:
            continue
        url = urllib.parse.urljoin(page_url, str(anchor.get("href") or "").strip()).split("#")[0]
        title = " ".join(anchor.get_text(" ", strip=True).split())
        if urllib.parse.urlparse(url).netloc != host or len(title) < 8:
            continue
        found[url] = {"title": title[:300], "url": url, "date": date, "source_url": page_url}
    return list(found.values())[:30]


SITEMAP_CHILD = re.compile(r"nyhet|news|post|blog|artik|aktuelt|karriere|career|jobb|stilling", re.I)
SITEMAP_NOT_NEWS = re.compile(r"page|categor|tag|author|product|taxonom", re.I)  # WordPress also publishes wp-sitemap-posts-page-1.xml
MAX_SITEMAP_ITEMS = 12
MAX_FEED_ITEMS = 30


def _feed_field(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", block, re.S | re.I)
    if not match:
        return ""
    text = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", match.group(1).strip())
    return html_module.unescape(re.sub(r"<[^>]+>", " ", text)).strip()


def _feed_date(value: str) -> str | None:
    value = value.strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", value):
        return value[:10]
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError):
        return None


def feed_items(soup: BeautifulSoup, home_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """Dated posts from the RSS or Atom feed the site itself advertises: real titles and publication dates."""
    result: dict[str, Any] = {"source_url": None, "content_sha256": None, "items": []}
    link = soup.select_one('link[rel="alternate"][type*="rss"], link[rel="alternate"][type*="atom"]')
    if not link or not link.get("href"):
        return result
    try:
        url = urllib.parse.urljoin(home_url, str(link["href"]))
        domain = _registered_domain(home_url)
        if _registered_domain(url) != domain:
            return result
        text, digest = _fetch_text(url, timeout)
        if not text:
            return result
        result.update({"source_url": url, "content_sha256": digest})
        for block in re.split(r"<(?:item|entry)[\s>]", text)[1:]:
            block = block.split("</item>", 1)[0].split("</entry>", 1)[0]
            atom_link = re.search(r'<link[^>]*href="([^"]+)"', block, re.I)
            item_url = _feed_field(block, "link") or (atom_link.group(1) if atom_link else "")
            date = _feed_date(_feed_field(block, "pubDate") or _feed_field(block, "published") or _feed_field(block, "updated"))
            title = " ".join(_feed_field(block, "title").split())
            if not (item_url and date and title):
                continue
            if _registered_domain(item_url) != domain:
                continue
            result["items"].append({"title": title[:300], "url": item_url, "date": date, "source_url": url, "date_basis": "feed_publication_date"})
            if len(result["items"]) >= MAX_FEED_ITEMS:
                break
    except Exception:
        return result
    return result


def _fetch_text(url: str, timeout: float, max_bytes: int = 1_000_000) -> tuple[str, str | None]:
    """Fetch a small text resource when robots.txt allows it; returns (text, sha256 or None)."""
    if not _robots_allowed(url, timeout):
        return "", None
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/xml,text/xml"})
    with SAFE_OPENER.open(request, timeout=timeout) as response:
        raw = _read_capped(response, max_bytes + 1)
    if len(raw) > max_bytes:
        return "", None
    return raw.decode("utf-8", errors="replace"), __import__("hashlib").sha256(raw).hexdigest()


def sitemap_items(home_url: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """News and job URLs the site lists in its own sitemap, with its stated lastmod. Bounded and optional."""
    result: dict[str, Any] = {"source_url": None, "content_sha256": None, "documents": [], "news": [], "jobs": []}
    parsed_home = urllib.parse.urlparse(home_url)
    root_sitemap = f"{parsed_home.scheme}://{parsed_home.netloc}/sitemap.xml"
    domain = _registered_domain(home_url)
    try:
        text, digest = _fetch_text(root_sitemap, timeout)
        if not text:
            return result
        result.update({"source_url": root_sitemap, "content_sha256": digest})
        documents: list[tuple[str, str | None, str, str | None]] = [(text, None, root_sitemap, digest)]
        if "<sitemapindex" in text:
            children = [item for item in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text) if SITEMAP_CHILD.search(item) and not SITEMAP_NOT_NEWS.search(item)][:3]
            documents = []
            for child in children:
                if _registered_domain(child) != domain:
                    continue
                child_text, child_digest = _fetch_text(child, timeout)
                if child_text:
                    documents.append((child_text, "jobs" if re.search(r"karriere|career|jobb|stilling", child, re.I) else "news", child, child_digest))
        result["documents"] = [{"url": source, "sha256": source_digest} for _, _, source, source_digest in documents]
        entries: list[tuple[str, str | None, str | None, str]] = []
        for document, kind, source, _ in documents:
            for block in (piece.split("</url>", 1)[0] for piece in document.split("<url>")[1:]):
                loc = re.search(r"<loc>\s*([^<\s]+)\s*</loc>", block)
                lastmod = re.search(r"<lastmod>\s*(\d{4}-\d{2}-\d{2})", block)
                if loc and _registered_domain(loc.group(1)) == domain:
                    entries.append((loc.group(1), lastmod.group(1) if lastmod else None, kind, source))
    except Exception:
        return result
    entries.sort(key=lambda item: item[1] or "", reverse=True)
    listing_titles = {"personvern", "informasjonskapsler", "cookies", "privacy", "vilkar", "kontakt", "om oss", "news", "nyheter", "nyhet", "blog", "blogg", "bloggen", "aktuelt", "artikler", "karriere", "career", "careers", "jobb", "jobs", "stillinger", "ledige stillinger", "jobb hos oss"}
    for url, lastmod, kind, source in entries:
        path = urllib.parse.urlparse(url).path
        path_date = re.search(r"/(20\d{2})/(0?[1-9]|1[0-2])/(0?[1-9]|[12]\d|3[01])(?:/|$)", path)
        basis = "sitemap_lastmod"
        if path_date:
            lastmod = "%s-%02d-%02d" % (path_date.group(1), int(path_date.group(2)), int(path_date.group(3)))
            basis = "date_in_url"
        depth = len([part for part in path.split("/") if part])
        title = re.sub(r"[-_]+", " ", urllib.parse.unquote(path.rstrip("/").rsplit("/", 1)[-1])).strip()
        if title.casefold() in listing_titles or not title:
            continue
        if depth >= 2 and (kind == "jobs" or JOB_PATH.search(path)) and _is_job_link(title, path) and len(result["jobs"]) < MAX_SITEMAP_ITEMS:
            result["jobs"].append({"title": title, "date_posted": None, "valid_through": None, "source_url": source, "posting_url": url, "sitemap_lastmod": lastmod, "date_basis": basis})
        elif lastmod and depth >= 1 and (kind == "news" or (depth >= 2 and NEWS_PATH.search(path))) and len(result["news"]) < MAX_SITEMAP_ITEMS:
            result["news"].append({"title": title, "url": url, "date": lastmod, "source_url": source, "date_basis": basis})
    return result


def job_postings(html_metadata: dict[str, Any], page_url: str) -> list[dict[str, Any]]:
    """schema.org JobPosting records a page publishes about itself."""
    postings: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            kind = node.get("@type")
            if "JobPosting" in (kind if isinstance(kind, list) else [kind]):
                postings.append({
                    "title": str(node.get("title") or "")[:300],
                    "date_posted": str(node.get("datePosted") or "")[:10] or None,
                    "valid_through": str(node.get("validThrough") or "")[:10] or None,
                    "source_url": page_url,
                })
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(html_metadata.get("json-ld", []))
    return [item for item in postings if item["title"]]


def assert_public_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host:
        raise ValueError("Only public HTTP(S) URLs are allowed")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local hosts are blocked")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ValueError("Hostname did not resolve") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local, multicast, and reserved addresses are blocked")


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        assert_public_url(newurl)
        old, new = urllib.parse.urlparse(req.full_url), urllib.parse.urlparse(newurl)
        if new.netloc.lower() != old.netloc.lower() and old.path != "/robots.txt" and not _robots_allowed(newurl, 10.0, if_unreachable=True):
            raise urllib.error.URLError("redirect target disallowed by robots.txt")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = urllib.request.build_opener(SafeRedirectHandler())


def normalize_homepage(value: str | None) -> str | None:
    value = str(value or "").strip()
    if not value:
        return None
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path or "/", "", "", ""))


def _registered_domain(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = _TLD_EXTRACT(parsed.hostname or "")
    return ext.top_domain_under_public_suffix


def _robots_allowed(url: str, timeout: float, *, if_unreachable: bool = False, _depth: int = 0) -> bool:
    assert_public_url(url)
    parsed = urllib.parse.urlparse(url)
    robots_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = urllib.request.Request(robots_url, headers={"User-Agent": USER_AGENT})
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            final = urllib.parse.urlparse(response.geturl())
            content_type = response.headers.get("content-type", "").lower()
            body = _read_capped(response, 500_000).decode("utf-8", errors="replace")
        if final.path != "/robots.txt" or "html" in content_type:
            # A redirect or HTML answer is not a robots file. If it moved to another host, that host's own robots.txt decides.
            if final.netloc.lower() != parsed.netloc.lower() and _depth == 0:
                return _robots_allowed(urllib.parse.urlunparse((final.scheme, final.netloc, parsed.path or "/", "", "", "")), timeout, if_unreachable=if_unreachable, _depth=1)
            return True
        parser.parse(body.splitlines())
        return parser.can_fetch(USER_AGENT, url)
    except urllib.error.HTTPError as exc:
        return exc.code < 500  # RFC 9309: a missing robots.txt allows crawling, a server error does not
    except Exception:
        return if_unreachable  # an unreachable robots.txt is not permission to crawl deeper than one homepage GET


def _social_links(base_url: str, soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}
    candidates = [str(node.get("href") or "") for node in soup.select("a[href]")]
    candidates.extend(str(node.get("data-href") or "") for node in soup.select("[data-href]"))
    candidates.extend(str(node.get("src") or "") for node in soup.select("iframe[src]"))
    for candidate in candidates:
        try:
            url = urllib.parse.urljoin(base_url, candidate)
            parsed_candidate = urllib.parse.urlparse(url)
        except ValueError:
            continue
        if (parsed_candidate.hostname or "").casefold().removeprefix("www.") == "facebook.com" and parsed_candidate.path.startswith("/plugins/"):
            embedded = urllib.parse.parse_qs(parsed_candidate.query).get("href", [])
            if embedded:
                url = embedded[0]
        normalized = normalize_social_url(url)
        if not normalized:
            continue
        found[(normalized["platform"], normalized["url"])] = normalized
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def structured_social_links(value: Any) -> list[dict[str, str]]:
    found: dict[tuple[str, str], dict[str, str]] = {}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            same_as = node.get("sameAs")
            urls = same_as if isinstance(same_as, list) else [same_as]
            for raw in urls:
                if not isinstance(raw, str):
                    continue
                normalized = normalize_social_url(raw.strip())
                if normalized:
                    found[(normalized["platform"], normalized["url"])] = normalized
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return sorted(found.values(), key=lambda item: (item["platform"], item["url"]))


def normalize_social_url(url: str) -> dict[str, str] | None:
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    platform = next((label for domain, label in SOCIAL_HOSTS.items() if host == domain or host.endswith("." + domain)), None)
    if not platform:
        return None
    parts = [part.strip() for part in parsed.path.split("/") if part.strip()]
    lowered = [part.casefold() for part in parts]
    rejected_first = {
        "facebook": {"sharer", "sharer.php", "share.php", "dialog", "policy.php", "privacy", "events", "groups", "plugins"},
        "instagram": {"p", "reel", "reels", "stories", "explore"},
        "x": {"intent", "share", "home", "search", "i"},
    }
    if not parts or lowered[0] in rejected_first.get(platform, set()):
        return None
    if platform == "facebook" and lowered[0] == "profile.php":
        return None
    if platform == "linkedin" and (lowered[0] != "company" or len(parts) < 2):
        return None
    if platform == "youtube" and lowered[0] not in {"channel", "user", "c"} and not parts[0].startswith("@"):
        return None
    if host == "youtu.be":
        return None
    if platform == "tiktok" and not parts[0].startswith("@"):
        return None
    if platform == "x" and len(parts) != 1:
        return None
    canonical_host = {
        "linkedin": "linkedin.com",
        "facebook": "facebook.com",
        "instagram": "instagram.com",
        "x": "x.com",
        "youtube": "youtube.com",
        "tiktok": "tiktok.com",
    }[platform]
    if platform == "linkedin":
        parts = parts[:2]
    elif platform == "youtube":
        parts = parts[:1] if parts[0].startswith("@") else parts[:2]
    return {"platform": platform, "url": f"https://{canonical_host}/{'/'.join(parts)}"}


def _priority_links(base_url: str, soup: BeautifulSoup, limit: int = MAX_SECONDARY_PAGES) -> list[str]:
    base = urllib.parse.urlparse(base_url)
    candidates: dict[str, int] = {}
    for anchor in soup.select("a[href]"):
        href = str(anchor.get("href") or "").strip()
        try:
            url = urllib.parse.urljoin(base_url, href)
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            continue
        if parsed.scheme not in {"http", "https"} or parsed.netloc.lower() != base.netloc.lower():
            continue
        haystack = (parsed.path + " " + anchor.get_text(" ", strip=True)).casefold()
        rank = next((index for index, term in enumerate(PRIORITY_TERMS) if term in haystack), None)
        if rank is None:
            continue
        clean = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, urllib.parse.quote(parsed.path or "/", safe="/%:@"), "", "", ""))
        if clean.rstrip("/") == base_url.rstrip("/"):
            continue
        candidates[clean] = min(rank, candidates.get(clean, rank))
    return [url for url, _ in sorted(candidates.items(), key=lambda item: (item[1], item[0]))[:limit]]


def _fetch_secondary_page(url: str, *, homepage_domain: str, timeout: float, max_bytes: int, org: str | None = None, hints: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, list[dict[str, str]], int, int, int, str | None]:
    if not _robots_allowed(url, timeout):
        return None, [], 1, 0, 0, "robots.txt disallows page"
    started = time.monotonic()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            raw = _read_capped(response, max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            final_url = response.geturl()
            if len(raw) > max_bytes or "html" not in response.headers.get("content-type", "").lower():
                return None, [], 2, len(raw), elapsed, "unsupported or oversized page"
            if _registered_domain(final_url) != homepage_domain:
                return None, [], 2, len(raw), elapsed, "redirected outside registered domain"
        page_html = _decode_html(raw)
        page_soup = BeautifulSoup(page_html, "lxml")
        page_text = trafilatura.extract(page_html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        try:
            page_metadata = extruct.extract(page_html, base_url=final_url, syntaxes=["json-ld"], errors="log")
        except Exception:
            page_metadata = {}
        page = {
            "url": final_url,
            "title": page_soup.title.get_text(" ", strip=True)[:500] if page_soup.title else "",
            "main_text_excerpt": page_text[:5000],
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "stated_dates": _page_dates(page_metadata, page_soup),
            "job_postings": job_postings(page_metadata, final_url),
            "kind": "jobs" if JOB_PATH.search(final_url) else "news" if NEWS_PATH.search(urllib.parse.urlparse(final_url).path) else "other",
            "listed_jobs": listed_jobs(page_soup, final_url) if JOB_PATH.search(final_url) else [],
            "job_board_links": job_board_links(page_soup, final_url) if JOB_PATH.search(final_url) else [],
            "news_items": news_items(page_soup, final_url) if NEWS_PATH.search(urllib.parse.urlparse(final_url).path) else [],
            "org_number_excerpt": find_org_number(page_soup.get_text(" ", strip=True), org, (hints or {}).get("name_tokens")),
            "address_excerpt": find_address(page_soup.get_text(" ", strip=True), hints),
            "phone_excerpt": find_phone(page_soup.get_text(" ", strip=True), hints),
        }
        return page, _social_links(final_url, page_soup), 2, len(raw), elapsed, None
    except Exception as exc:
        return None, [], 2, 0, int((time.monotonic() - started) * 1000), f"{type(exc).__name__}: {str(exc)[:120]}"


def _jsonld_organisations(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            kind = value.get("@type")
            kinds = {item for item in (kind if isinstance(kind, list) else [kind]) if isinstance(item, str)}
            if kinds & {"Organization", "Corporation", "LocalBusiness", "Store", "Restaurant"}:
                values.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(metadata.get("json-ld", []))
    return values[:20]


def _extraction_state(text: str, soup: BeautifulSoup) -> str:
    return "js_fallback_candidate" if len(text.strip()) < 100 and len(soup.select("script[src]")) >= 2 else "static_complete"


def fetch_website(url: str | None, *, timeout: float = 15.0, max_bytes: int = 2_000_000, org: str | None = None, hints: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    supplied_url = str(url or "").strip()
    supplied_scheme = bool(re.match(r"^https?://", supplied_url, re.I))
    normalized = normalize_homepage(url)
    if not normalized:
        return evidence("website", "not_found", "registry_linked_company_website", "https://data.brreg.no/enhetsregisteret/api/enheter", note="No valid registry website URL"), {"requests": 0, "bytes": 0, "latencies_ms": []}
    try:
        assert_public_url(normalized)
    except ValueError as exc:
        status = "not_found" if "resolve" in str(exc) else "blocked"  # a dead domain did not refuse us
        return evidence("website", status, "registry_linked_company_website", normalized, note=str(exc)), {"requests": 0, "bytes": 0, "latencies_ms": []}
    if not _robots_allowed(normalized, timeout, if_unreachable=True):
        return evidence("website", "blocked", "registry_linked_company_website", normalized, note="robots.txt disallows this user agent"), {"requests": 1, "bytes": 0, "latencies_ms": []}
    started = time.monotonic()
    request = urllib.request.Request(normalized, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    try:
        with SAFE_OPENER.open(request, timeout=timeout) as response:
            content_type = response.headers.get("content-type", "")
            raw = _read_capped(response, max_bytes + 1)
            elapsed = int((time.monotonic() - started) * 1000)
            if len(raw) > max_bytes:
                return evidence("website", "blocked", "registry_linked_company_website", normalized, note="Homepage exceeds byte limit"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            if "html" not in content_type.lower():
                return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"Unsupported content type: {content_type}"), {"requests": 2, "bytes": len(raw), "latencies_ms": [elapsed]}
            final_url = response.geturl()
            assert_public_url(final_url)
        html = _decode_html(raw)
        soup = BeautifulSoup(html, "lxml")
        try:
            structured = extruct.extract(html, base_url=final_url, syntaxes=["json-ld", "microdata", "opengraph"], errors="log")
        except Exception:
            structured = {}
        text = trafilatura.extract(html, url=final_url, include_links=False, include_tables=False, favor_precision=True) or ""
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        description_tag = soup.select_one('meta[name="description"], meta[property="og:description"]')
        description = str(description_tag.get("content") or "").strip() if description_tag else ""
        value = {
            "requested_url": normalized,
            "final_url": final_url,
            "registered_domain": _registered_domain(final_url),
            "title": title[:500],
            "description": description[:2000],
            "main_text_excerpt": text[:5000],
            "social_links": _social_links(final_url, soup),
            "structured_organisations": _jsonld_organisations(structured),
            "content_sha256": __import__("hashlib").sha256(raw).hexdigest(),
            "extraction_state": _extraction_state(text, soup),
        }
        home_proof = find_org_number(soup.get_text(" ", strip=True), org, (hints or {}).get("name_tokens"))
        pages = [{"url": final_url, "title": title[:500], "main_text_excerpt": text[:5000], "content_sha256": value["content_sha256"], "stated_dates": _page_dates(structured, soup), "job_postings": job_postings(structured, final_url), "kind": "home", "org_number_excerpt": home_proof, "address_excerpt": find_address(soup.get_text(" ", strip=True), hints), "phone_excerpt": find_phone(soup.get_text(" ", strip=True), hints)}]
        social = value["social_links"]
        crawl_errors = []
        requests = 2
        bytes_received = len(raw)
        page_latencies = [elapsed]
        homepage_domain = value["registered_domain"]
        site_deadline = time.monotonic() + SITE_BUDGET_SECONDS
        for page_url in _priority_links(final_url, soup):
            if time.monotonic() > site_deadline:
                crawl_errors.append({"url": page_url, "error": "site time budget reached; later pages not read"})
                continue
            page, page_social, page_requests, page_bytes, page_elapsed, page_error = _fetch_secondary_page(
                page_url,
                homepage_domain=homepage_domain,
                timeout=timeout,
                max_bytes=min(max_bytes, 1_000_000),
                org=org,
                hints=hints,
            )
            requests += page_requests
            bytes_received += page_bytes
            if page_elapsed:
                page_latencies.append(page_elapsed)
            if page:
                pages.append(page)
                social.extend(page_social)
            elif page_error:
                crawl_errors.append({"url": page_url, "error": page_error})
        value["pages"] = pages
        value["sitemap"] = sitemap_items(final_url, timeout=min(timeout, 10.0)) if time.monotonic() <= site_deadline else {"status": "skipped", "note": "site time budget reached"}
        value["feed"] = feed_items(soup, final_url, timeout=min(timeout, 10.0)) if time.monotonic() <= site_deadline else {"items": []}
        address_page = next((page for page in pages if page.get("address_excerpt")), None)
        if address_page:
            value["registered_address_found_at"] = address_page["url"]
            value["registered_address_excerpt"] = address_page["address_excerpt"]
        phone_page = next((page for page in pages if page.get("phone_excerpt")), None)
        if phone_page:
            value["registered_phone_found_at"] = phone_page["url"]
            value["registered_phone_excerpt"] = phone_page["phone_excerpt"]
        proof_page = next((page for page in pages if page.get("org_number_excerpt")), None)
        if proof_page:
            value["org_number_found"] = org
            value["org_number_found_at"] = proof_page["url"]
            value["identity_text_excerpt"] = proof_page["org_number_excerpt"]
        value["social_links"] = list({(item["platform"], item["url"]): item for item in social}.values())
        value["crawl_errors"] = crawl_errors
        return evidence("website", "available", "registry_linked_company_website", final_url, value=value, note="Company-controlled claim layer; not an official registry fact", content_sha256=value["content_sha256"]), {"requests": requests, "bytes": bytes_received, "latencies_ms": page_latencies}
    except urllib.error.HTTPError as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        status = "not_found" if exc.code in {404, 410} else "source_error"
        return evidence("website", status, "registry_linked_company_website", normalized, note=f"HTTP {exc.code}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except urllib.error.URLError as exc:
        if not supplied_scheme and normalized.startswith("https://"):
            first_elapsed = int((time.monotonic() - started) * 1000)
            record, metrics = fetch_website("http://" + supplied_url, timeout=timeout, max_bytes=max_bytes, org=org, hints=hints)
            metrics["requests"] += 2
            metrics["latencies_ms"].insert(0, first_elapsed)
            return record, metrics
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"URLError: {str(exc.reason)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return evidence("website", "source_error", "registry_linked_company_website", normalized, note=f"{type(exc).__name__}: {str(exc)[:180]}"), {"requests": 2, "bytes": 0, "latencies_ms": [elapsed]}
