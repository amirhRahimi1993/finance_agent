# forex-agent

A small Python 3.12+ collector for EUR/USD research data. It downloads English ECB monetary policy statement transcripts and Federal Reserve FOMC minutes from official public pages. It uses the GDELT DOC API for article metadata only and does not fetch article bodies.

## Install

```sh
uv sync
```

## Run

```sh
uv run python src/data_collection.py
uv run python src/data_collection.py --document-count 5 --news-limit 10
```

The defaults request 10 documents **per institution** and up to 20 news records. `--news-limit 0` skips GDELT. `--data-dir PATH` chooses another output directory. Each run writes UTF-8 `data/documents.jsonl` and `data/news.jsonl`, replacing the previous files. Review the printed error list if an upstream source is unavailable.

The ECB collector follows the official statement index's yearly listing links. The Fed collector follows the official FOMC calendar's HTML minutes links and uses the calendar's explicit minutes release dates. Each document record contains the page's actual text. Empty pages are discarded. URLs determine stable IDs; repeated IDs are discarded. HTTP requests use timeouts, retries for transient failures, and per-host delays. The collector checks `robots.txt` before retrieving pages and honors its crawl delay; a missing `robots.txt` (HTTP 404/410) is treated as no restrictions.

## Chunk documents

```sh
uv run python src/chunking.py
uv run python src/chunking.py --chunk-size 1000 --chunk-overlap 200
```

The script reads each UTF-8 JSONL document separately, splits only its `text`, and writes `data/chunks.jsonl`. Each chunk retains its document ID and source metadata. Use `--input` and `--output` to choose different files. The generated chunk file is ignored by Git.

GDELT returns a `seendate` (when GDELT first observed an article), which is **not** necessarily its publication time. The collector stores that value as optional `seen_at`. It sets `published_at` to null unless GDELT supplies a distinct publication timestamp. News `source` is GDELT's publisher domain when present, otherwise the article URL's host. `related_currencies` is `["EUR", "USD"]` because the query targets that pair; it is a query tag, not a claim that both currencies appear in every article.

## Test

```sh
uv run pytest -q
```

Tests use local HTML and API response fixtures; they make no live network calls.
