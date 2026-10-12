"""Split the collected document text into provenance-aware JSONL chunks."""

import argparse
import json
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class chunking:
    def __init__(self, chunk_size=1000, chunk_overlap=200):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            length_function=len,
        )

    def split_text(self, text):
        return self.text_splitter.split_text(text)

    def split_json(self, json_data):
        """Split a document's text, not its serialized metadata."""
        return self.split_text(json_data["text"])


def read_json_file(file_path):
    """Read UTF-8 JSON Lines; each nonblank line is one document."""
    documents = []
    with Path(file_path).open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                document = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_number} of {file_path}: {exc}") from exc
            if not isinstance(document, dict) or not isinstance(document.get("id"), str) or not isinstance(document.get("text"), str) or not document["text"].strip():
                raise ValueError(f"Missing id or nonempty text on line {line_number} of {file_path}")
            documents.append(document)
    return documents


def chunk_file(input_path, output_path, chunk_size=1000, chunk_overlap=200):
    documents = read_json_file(input_path)
    splitter = chunking(chunk_size, chunk_overlap)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunk_count = 0
    with output_path.open("w", encoding="utf-8", newline="\n") as output:
        for document in documents:
            for index, text in enumerate(splitter.split_json(document)):
                chunk = {
                    "id": f"{document['id']}-chunk-{index:04d}",
                    "document_id": document["id"],
                    "chunk_index": index,
                    "source": document.get("source"),
                    "title": document.get("title"),
                    "published_at": document.get("published_at"),
                    "url": document.get("url"),
                    "text": text,
                }
                output.write(json.dumps(chunk, ensure_ascii=False) + "\n")
                chunk_count += 1
    return len(documents), chunk_count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PROJECT_ROOT / "data" / "documents.jsonl")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "data" / "chunks.jsonl")
    parser.add_argument("--chunk-size", type=int, default=1000)
    parser.add_argument("--chunk-overlap", type=int, default=200)
    args = parser.parse_args()
    if args.chunk_size <= 0 or not 0 <= args.chunk_overlap < args.chunk_size:
        parser.error("chunk size must be positive and overlap must be smaller than chunk size")
    document_count, chunk_count = chunk_file(args.input, args.output, args.chunk_size, args.chunk_overlap)
    print(f"Wrote {chunk_count} chunks from {document_count} documents to {args.output}")


if __name__ == "__main__":
    main()
