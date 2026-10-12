import json

import pytest

from chunking import chunk_file, read_json_file


def test_chunking_reads_utf8_jsonl_and_keeps_provenance(tmp_path):
    source = tmp_path / "documents.jsonl"
    documents = [
        {"id": "ecb-1", "source": "ECB", "title": "Euro € policy", "published_at": "2026-09-10", "url": "https://example.org/ecb", "text": "Inflation affects euro area policy. " * 12},
        {"id": "fed-1", "source": "Fed", "title": "Minutes", "published_at": None, "url": "https://example.org/fed", "text": "Interest rates affect the dollar. " * 12},
    ]
    source.write_text("\n".join(json.dumps(doc, ensure_ascii=False) for doc in documents) + "\n", encoding="utf-8")
    output = tmp_path / "chunks.jsonl"

    document_count, chunk_count = chunk_file(source, output, chunk_size=100, chunk_overlap=20)
    chunks = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]

    assert document_count == 2
    assert chunk_count == len(chunks) > 2
    assert {chunk["document_id"] for chunk in chunks} == {"ecb-1", "fed-1"}
    assert chunks[0]["title"] == "Euro € policy"
    assert chunks[0]["text"].startswith("Inflation affects")
    assert all(len(chunk["text"]) <= 100 for chunk in chunks)
    assert len({chunk["id"] for chunk in chunks}) == len(chunks)


def test_jsonl_error_identifies_line(tmp_path):
    source = tmp_path / "documents.jsonl"
    source.write_text('{"id":"ok","text":"yes"}\n{bad json}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        read_json_file(source)
