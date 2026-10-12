"""Collect official monetary-policy text and GDELT news metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

ECB_INDEX = "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/html/index.en.html"
FED_INDEX = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
GDELT_API = "https://api.gdeltproject.org/api/v2/doc/doc"
USER_AGENT = "forex-agent/0.1 (research data collector; respects robots.txt)"
ROOT = Path(__file__).resolve().parents[1]


def canonical_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), re.sub(r"/{2,}", "/", parsed.path), parsed.query, ""))


def stable_id(source: str, url: str) -> str:
    return f"{source.lower()}-{hashlib.sha256(canonical_url(url).encode('utf-8')).hexdigest()[:20]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_date(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%B %d, %Y", "%Y%m%dT%H%M%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
        except ValueError:
            pass
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return None


class Fetcher:
    def __init__(self, client: httpx.Client, *, retries: int = 2, delays: dict[str, float] | None = None, sleep=time.sleep, clock=time.monotonic):
        self.client = client
        self.retries = retries
        self.delays = delays or {"www.ecb.europa.eu": 5.0, "www.federalreserve.gov": 1.0, "api.gdeltproject.org": 5.0}
        self.sleep = sleep
        self.clock = clock
        self.last_request: dict[str, float] = {}
        self.robots: dict[str, RobotFileParser | None] = {}

    def _throttle(self, host: str) -> None:
        gap = self.delays.get(host, 1.0)
        remaining = gap - (self.clock() - self.last_request.get(host, -1e9))
        if remaining > 0:
            self.sleep(remaining)
        self.last_request[host] = self.clock()

    def _request(self, url: str, params: dict | None = None) -> httpx.Response:
        host = urlsplit(url).netloc
        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                response = self.client.get(url, params=params)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    self.sleep(min(2 ** attempt, 8))
                    continue
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == self.retries:
                    raise
                self.sleep(min(2 ** attempt, 8))
        raise RuntimeError("unreachable")

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self.robots:
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            try:
                response = self._request(robots_url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (404, 410):
                    self.robots[host] = None
                else:
                    raise RuntimeError(f"robots.txt unavailable for {host}: {exc}") from exc
            else:
                parser = RobotFileParser()
                parser.parse(response.text.splitlines())
                self.robots[host] = parser
                if parser.crawl_delay(USER_AGENT) is not None:
                    self.delays[host] = max(self.delays.get(host, 0), float(parser.crawl_delay(USER_AGENT)))
        parser = self.robots[host]
        return parser is None or parser.can_fetch(USER_AGENT, url)

    def get(self, url: str, *, params: dict | None = None, check_robots: bool = True) -> httpx.Response:
        if check_robots and not self._allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        response = self._request(url, params)
        if response.url.host != urlsplit(url).netloc:
            raise PermissionError(f"unexpected redirect to {response.url}")
        return response


def ecb_candidates(index_html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(index_html, "html.parser")
    listing = soup.select_one("dl[data-snippets]")
    if not listing:
        return []
    return [urljoin(base_url, path.strip()) for path in listing["data-snippets"].split(",")]


def ecb_links(snippet_html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(snippet_html, "html.parser")
    result = []
    for dt in soup.select("dt[isodate]"):
        dd = dt.find_next_sibling("dd")
        link = dd.select_one(".title a[href$='.en.html']") if dd else None
        if link:
            result.append({"url": canonical_url(urljoin(base_url, link["href"])), "title": link.get_text(" ", strip=True), "date": parse_date(dt.get("isodate"))})
    return result


def fed_links(index_html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(index_html, "html.parser")
    result = []
    for meeting in soup.select(".fomc-meeting"):
        minutes = meeting.select_one(".fomc-meeting__minutes")
        if not minutes:
            continue
        link = minutes.find("a", href=re.compile(r"/fomcminutes\d{8}\.htm$"))
        if not link:
            continue
        match = re.search(r"Released\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", minutes.get_text(" ", strip=True))
        result.append({"url": canonical_url(urljoin(base_url, link["href"])), "date": parse_date(match.group(1)) if match else None})
    return sorted(result, key=lambda item: item["url"], reverse=True)


def extract_document(html: str, source: str, url: str, listed_date: str | None = None, listed_title: str | None = None) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    if source == "ECB":
        container = soup.select_one("main .section")
        title_tag = soup.select_one("main h1")
        date_tag = soup.select_one("meta[name='citation_online_date']")
        published_at = parse_date(date_tag.get("content")) if date_tag else listed_date
    else:
        container = soup.select_one("#article")
        title_tag = container.find(["h1", "h2", "h3"]) if container else None
        published_at = listed_date
    if not container:
        return None
    for tag in container.select("script, style, nav, footer, aside, .share, .social-share, .related, .footnotes"):
        tag.decompose()
    title = listed_title or (title_tag.get_text(" ", strip=True) if title_tag else None)
    text = "\n".join(line.strip() for line in container.get_text("\n", strip=True).splitlines() if line.strip())
    if not title or not text.strip():
        return None
    return {"id": stable_id(source, url), "source": source, "title": title, "published_at": published_at, "url": canonical_url(url), "text": text, "collected_at": utc_now()}


def news_records(payload: dict, limit: int) -> tuple[list[dict], Counter]:
    records = []
    discarded = Counter()
    seen = set()
    for article in payload.get("articles", []):
        url, title = article.get("url"), article.get("title")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or not isinstance(title, str) or not title.strip():
            discarded["invalid"] += 1
            continue
        url = canonical_url(url)
        identifier = stable_id("GDELT", url)
        if identifier in seen:
            discarded["duplicate"] += 1
            continue
        seen.add(identifier)
        source = article.get("domain") or urlsplit(url).netloc
        seen_at = parse_date(article.get("seendate"))
        published_at = parse_date(article.get("published_at") or article.get("published"))
        record = {"id": identifier, "title": title.strip(), "source": source, "published_at": published_at, "url": url, "related_currencies": ["EUR", "USD"], "collected_at": utc_now()}
        if seen_at:
            record["seen_at"] = seen_at
        records.append(record)
        if len(records) >= limit:
            break
    return records, discarded


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def collect(fetcher: Fetcher, document_count: int, news_limit: int, data_dir: Path) -> dict:
    stats = {"ECB": 0, "Fed": 0, "news": 0, "duplicate": 0, "invalid": 0, "missing_published_at": 0, "errors": []}
    documents = []
    seen = set()
    for source, index in (("ECB", ECB_INDEX), ("Fed", FED_INDEX)):
        try:
            page = fetcher.get(index)
            if source == "ECB":
                candidates = []
                for snippet_url in ecb_candidates(page.text, index):
                    try:
                        candidates.extend(ecb_links(fetcher.get(snippet_url).text, snippet_url))
                    except (httpx.HTTPError, PermissionError, RuntimeError) as exc:
                        stats["errors"].append(f"ECB listing {snippet_url}: {exc}")
                    if len(candidates) >= document_count + 5:
                        break
            else:
                candidates = fed_links(page.text, index)
            for candidate in candidates:
                if stats[source] >= document_count:
                    break
                url = candidate["url"]
                identifier = stable_id(source, url)
                if identifier in seen:
                    stats["duplicate"] += 1
                    continue
                seen.add(identifier)
                try:
                    record = extract_document(fetcher.get(url).text, source, url, candidate["date"], candidate.get("title"))
                except (httpx.HTTPError, PermissionError, RuntimeError) as exc:
                    stats["errors"].append(f"{source} document {url}: {exc}")
                    continue
                if record is None:
                    stats["invalid"] += 1
                    continue
                if record["published_at"] is None:
                    stats["missing_published_at"] += 1
                documents.append(record)
                stats[source] += 1
        except (httpx.HTTPError, PermissionError, RuntimeError) as exc:
            stats["errors"].append(f"{source} index {index}: {exc}")
    write_jsonl(data_dir / "documents.jsonl", documents)
    news = []
    if news_limit:
        try:
            response = fetcher.get(GDELT_API, params={"query": '("EUR/USD" OR "euro dollar") (ECB OR "Federal Reserve" OR inflation OR "interest rates")', "mode": "artlist", "format": "json", "maxrecords": min(max(news_limit * 2, 20), 250), "timespan": "7d", "sort": "datedesc"})
            news, discarded = news_records(response.json(), news_limit)
            stats.update({key: stats[key] + discarded[key] for key in ("duplicate", "invalid")})
            stats["missing_published_at"] += sum(item["published_at"] is None for item in news)
        except (httpx.HTTPError, PermissionError, RuntimeError, ValueError) as exc:
            stats["errors"].append(f"GDELT API {GDELT_API}: {exc}")
    write_jsonl(data_dir / "news.jsonl", news)
    stats["news"] = len(news)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-count", type=int, default=10, help="documents per institution")
    parser.add_argument("--news-limit", type=int, default=20)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    if args.document_count < 0 or not 0 <= args.news_limit <= 250:
        parser.error("document count must be nonnegative; news limit must be 0..250")
    timeout = httpx.Timeout(20.0, connect=10.0)
    with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        result = collect(Fetcher(client), args.document_count, args.news_limit, args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ECB"] == args.document_count and result["Fed"] == args.document_count else 1


if __name__ == "__main__":
    sys.exit(main())
