import json

import httpx
import pytest

from data_collection import (
    Fetcher,
    canonical_url,
    ecb_candidates,
    ecb_links,
    extract_document,
    fed_links,
    news_records,
    stable_id,
    write_jsonl,
)


@pytest.fixture
def ecb_html():
    index = '<dl data-snippets="../2026/html/index_include.en.html"></dl>'
    snippet = '''<dt isoDate="2026-09-10">10 September 2026</dt><dd><div class="title">
      <a href="/press/press_conference/monetary-policy-statement/2026/html/real.en.html">Monetary policy statement (with Q&amp;A)</a>
      </div></dd>'''
    document = '''<html><head><meta name="citation_online_date" content="10 September 2026"></head>
      <body><main><h1>PRESS CONFERENCE</h1><div class="section"><p>The Governing Council has decided
      to maintain its policy rates while monitoring inflation in the euro area.</p></div></main></body></html>'''
    return index, snippet, document


@pytest.fixture
def fed_html():
    index = '''<div class="fomc-meeting"><div class="fomc-meeting__minutes">Minutes:
      <a href="/monetarypolicy/fomcminutes20260916.htm">HTML</a> (Released October 07, 2026)
      </div></div>'''
    document = '''<div id="article"><h2>Minutes of the Federal Open Market Committee</h2>
      <p>Participants reviewed inflation, interest rates, and economic activity.</p>
      <p>The Committee discussed the outlook for monetary policy.</p></div>'''
    return index, document


def test_ecb_discovery_and_document(ecb_html):
    index, snippet, document = ecb_html
    index_url = "https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/html/index.en.html"
    snippets = ecb_candidates(index, index_url)
    assert snippets == ["https://www.ecb.europa.eu/press/press_conference/monetary-policy-statement/2026/html/index_include.en.html"]
    candidates = ecb_links(snippet, snippets[0])
    assert len(candidates) == 1
    record = extract_document(document, "ECB", candidates[0]["url"], candidates[0]["date"], candidates[0]["title"])
    assert record["title"] == "Monetary policy statement (with Q&A)"
    assert record["published_at"] == "2026-09-10T00:00:00+00:00"
    assert "monitoring inflation" in record["text"]


def test_fed_discovery_and_document(fed_html):
    index, document = fed_html
    candidate = fed_links(index, "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")[0]
    assert candidate["date"] == "2026-10-07T00:00:00+00:00"
    record = extract_document(document, "Fed", candidate["url"], candidate["date"])
    assert record["published_at"] == candidate["date"]
    assert "The Committee discussed" in record["text"]
    assert extract_document('<div id="article"></div>', "Fed", candidate["url"]) is None


def test_news_deduplicates_and_keeps_unknown_publication_date_null():
    article = {"url": "https://example.org/story#section", "title": "Euro and dollar", "domain": "example.org", "seendate": "20261012T120000Z"}
    rows, discarded = news_records({"articles": [article, article, {"title": "No URL"}]}, 20)
    assert len(rows) == 1
    assert discarded == {"duplicate": 1, "invalid": 1}
    assert rows[0]["published_at"] is None
    assert rows[0]["seen_at"] == "2026-10-12T12:00:00+00:00"
    assert rows[0]["related_currencies"] == ["EUR", "USD"]
    assert stable_id("GDELT", article["url"]) == stable_id("GDELT", canonical_url(article["url"]))


def test_jsonl_is_utf8(tmp_path):
    path = tmp_path / "news.jsonl"
    write_jsonl(path, [{"title": "€uro"}])
    assert json.loads(path.read_text(encoding="utf-8"))["title"] == "€uro"


def test_fetcher_retries_and_obeys_robots_and_rate_limit():
    calls = []
    responses = iter([503, 200])

    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nCrawl-delay: 5\nDisallow: /private")
        return httpx.Response(next(responses), text="ok")

    fake_time = [0.0]

    def sleep(seconds):
        fake_time[0] += seconds

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = Fetcher(client, retries=1, delays={"example.org": 0}, sleep=sleep, clock=lambda: fake_time[0])
        assert fetcher.get("https://example.org/public").text == "ok"
        with pytest.raises(PermissionError):
            fetcher.get("https://example.org/private")
    assert len(calls) == 3
    assert fake_time[0] >= 10
