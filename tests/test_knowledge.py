import json
from types import SimpleNamespace

import knowledge


class _FakeEmbedModel:
    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            lowered = str(text).lower()
            vectors.append([
                1.0 if "dijkstra" in lowered or "shortest path" in lowered else 0.1,
                1.0 if "osi" in lowered or "transport layer" in lowered else 0.1,
            ])
        return vectors


def test_knowledge_rebuild_records_chunk_provenance_and_answers_offline(monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "algorithms.md").write_text(
        "Dijkstra's algorithm finds the shortest path in a non-negative weighted graph."
    )
    (docs / "networks.txt").write_text("TCP operates at the transport layer of the OSI model.")
    index_file = tmp_path / "index.json"

    monkeypatch.setattr(knowledge, "DOCS_DIR", str(docs))
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())

    msg = knowledge.rebuild_knowledge_base()
    assert "indexed 2 chunks from 2 files" in msg
    saved = json.loads(index_file.read_text(encoding="utf-8"))
    assert all(isinstance(entry.get("chunk_id"), int) and entry["chunk_id"] >= 1 for entry in saved["entries"])

    offline_reply = knowledge.answer_from_knowledge("What finds the shortest path?", openai_client=None)
    assert "algorithms.md#chunk-1" in offline_reply
    assert "Dijkstra's algorithm" in offline_reply


def test_knowledge_handles_malformed_index_and_empty_llm_reply(monkeypatch, tmp_path):
    index_file = tmp_path / "index.json"
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())

    # Top-level JSON list instead of dict must not raise AttributeError:
    index_file.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert "do not have any indexed notes yet" in knowledge.answer_from_knowledge("question")

    # Corrupted entries without valid embeddings/text are skipped safely:
    index_file.write_text(json.dumps({"entries": [None, "bad", {"source": "a.txt", "text": "hi", "embedding": "bad"}]}),
                          encoding="utf-8")
    assert "empty or corrupted" in knowledge.answer_from_knowledge("question")

    # Valid entry with an empty LLM completion falls back to showing the retrieved source context:
    index_file.write_text(
        json.dumps({
            "entries": [{
                "source": "/tmp/algorithms.md",
                "chunk_id": 2,
                "text": "Dijkstra's algorithm uses a priority queue.",
                "embedding": [1.0, 0.1],
            }]
        }),
        encoding="utf-8",
    )
    empty_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="   "))])
            )
        )
    )
    fallback = knowledge.answer_from_knowledge("shortest path", openai_client=empty_client)
    assert "algorithms.md#chunk-2" in fallback
    assert "priority queue" in fallback


# --- Phase 7.1: bounded indexing, bounded retrieval, entry validation ----------


def _index_path(tmp_path):
    return tmp_path / "index.json"


def _point_docs_at(monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(knowledge, "DOCS_DIR", str(docs))
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(_index_path(tmp_path)))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())
    return docs


def test_knowledge_rebuild_bounds_file_count(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    for i in range(5):
        (docs / f"note{i}.txt").write_text(f"graph note {i} " * 20)
    monkeypatch.setattr(knowledge, "MAX_FILES", 3)

    msg = knowledge.rebuild_knowledge_base()

    assert "from 3 files" in msg
    saved = json.loads(_index_path(tmp_path).read_text(encoding="utf-8"))
    assert len(saved["entries"]) == 3


def test_knowledge_rebuild_bounds_file_bytes(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "big.txt").write_text("a" * 10_000)
    monkeypatch.setattr(knowledge, "MAX_FILE_BYTES", 1_000)

    knowledge.rebuild_knowledge_base()

    saved = json.loads(_index_path(tmp_path).read_text(encoding="utf-8"))
    assert [len(e["text"]) for e in saved["entries"]] == [1_000]


def test_knowledge_rebuild_bounds_extracted_text(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "long.md").write_text("0123456789" * 1_000)
    monkeypatch.setattr(knowledge, "MAX_EXTRACTED_CHARS", 1_500)

    knowledge.rebuild_knowledge_base()

    saved = json.loads(_index_path(tmp_path).read_text(encoding="utf-8"))
    # 1500 extracted chars chunked at 1200/200 overlap -> exactly two bounded chunks.
    assert len(saved["entries"]) == 2
    assert sum(len(e["text"]) for e in saved["entries"]) <= 1_500 + 200


def test_knowledge_rebuild_bounds_total_chunks(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "big.md").write_text("lorem ipsum " * 5_000)
    monkeypatch.setattr(knowledge, "MAX_TOTAL_CHUNKS", 5)

    msg = knowledge.rebuild_knowledge_base()

    assert "indexed 5 chunks" in msg
    saved = json.loads(_index_path(tmp_path).read_text(encoding="utf-8"))
    assert len(saved["entries"]) == 5


def test_knowledge_rebuild_refuses_oversized_index(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "a.md").write_text("dijkstra shortest path " * 50)
    monkeypatch.setattr(knowledge, "MAX_INDEX_BYTES", 100)

    msg = knowledge.rebuild_knowledge_base()

    assert "too large" in msg.lower()
    assert not _index_path(tmp_path).exists()


class _FakePdfPage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakePdfReader:
    def __init__(self, handle):
        self.pages = [_FakePdfPage(f"page {i} text") for i in range(1, 6)]


def test_knowledge_pdf_loading_is_bounded(monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(knowledge, "_pdf_reader_class", lambda: _FakePdfReader)
    monkeypatch.setattr(knowledge, "MAX_PDF_PAGES", 2)

    text = knowledge._load_pdf(str(pdf))

    assert "page 1 text" in text
    assert "page 2 text" in text
    assert "page 3 text" not in text

    monkeypatch.setattr(knowledge, "MAX_EXTRACTED_CHARS", 10)
    assert len(knowledge._load_pdf(str(pdf))) <= 10


def test_knowledge_retrieval_validates_entries(monkeypatch, tmp_path):
    index_file = _index_path(tmp_path)
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())
    monkeypatch.setattr(knowledge, "MAX_ENTRY_TEXT_CHARS", 50)
    index_file.write_text(
        json.dumps(
            {
                "entries": [
                    {"source": "/kb/good.md", "chunk_id": 1,
                     "text": "Dijkstra finds the shortest path.", "embedding": [1.0, 0.1]},
                    {"source": "/kb/short-dim.md", "chunk_id": 1,
                     "text": "embedding too short", "embedding": [1.0]},
                    {"source": "/kb/long-dim.md", "chunk_id": 1,
                     "text": "embedding too long", "embedding": [1.0, 0.1, 0.2]},
                    {"source": "/kb/nan.md", "chunk_id": 1,
                     "text": "nan embedding", "embedding": [float("nan"), 0.1]},
                    {"source": "/kb/inf.md", "chunk_id": 1,
                     "text": "infinite embedding", "embedding": [float("inf"), 0.1]},
                    {"source": "/kb/huge.md", "chunk_id": 1,
                     "text": "z" * 500, "embedding": [1.0, 0.1]},
                    {"source": "/kb/notext.md", "chunk_id": 1, "embedding": [1.0, 0.1]},
                ]
            }
        ),
        encoding="utf-8",
    )

    reply = knowledge.answer_from_knowledge("what finds the shortest path", openai_client=None)

    assert "good.md#chunk-1" in reply
    for bad in ("too short", "too long", "nan embedding", "infinite embedding", "huge.md", "notext"):
        assert bad not in reply


def test_knowledge_retrieval_context_is_bounded(monkeypatch, tmp_path):
    index_file = _index_path(tmp_path)
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())
    monkeypatch.setattr(knowledge, "MAX_CONTEXT_CHARS", 1_000)
    entries = [
        {"source": f"/kb/doc{i}.md", "chunk_id": 1,
         "text": "dijkstra shortest path " * 100, "embedding": [1.0, 0.1]}
        for i in range(4)
    ]
    index_file.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    reply = knowledge.answer_from_knowledge("shortest path", openai_client=None)

    assert "doc0.md#chunk-1" in reply
    assert "doc3.md" not in reply
    context = reply.split("offline):\n\n", 1)[1]
    assert len(context) <= 1_000 + 200  # snippet budget plus small label/join overhead


def test_knowledge_load_index_rejects_oversized_file(monkeypatch, tmp_path):
    index_file = _index_path(tmp_path)
    index_file.write_text(
        json.dumps({"entries": [{"source": "a.md", "text": "b", "embedding": [0.1, 0.2]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "MAX_INDEX_BYTES", 32)

    assert knowledge._load_index() is None


# --- Phase 7.1 follow-up: capacity reconciliation and pre-allocation bounds ----


def _representative_entry():
    """A realistic worst-case index entry: full-precision 384-dim embedding, max-size chunk."""
    return {
        "source": "/Users/owner/knowledge_docs/semester-notes/computer-networks.md",
        "chunk_id": 1,
        "text": "x" * 1200,
        "embedding": [((i * 37) % 199 - 99) / 97.0 for i in range(384)],
    }


def test_knowledge_index_capacity_is_consistent():
    entry_bytes = len(json.dumps(_representative_entry()).encode("utf-8"))
    effective_capacity = knowledge.MAX_INDEX_BYTES // entry_bytes

    # The advertised chunk limit must be reachable within the byte budget...
    assert effective_capacity >= knowledge.MAX_TOTAL_CHUNKS
    # ...and must not be set far below what the budget allows (headroom <= 2x).
    assert effective_capacity <= knowledge.MAX_TOTAL_CHUNKS * 2


def test_knowledge_rebuild_refuses_oversized_index_preserving_previous(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "a.md").write_text("dijkstra shortest path " * 50)
    index_file = _index_path(tmp_path)

    knowledge.rebuild_knowledge_base()
    original = index_file.read_bytes()
    size = len(original)

    # Exact boundary: an index that fits the budget exactly is written.
    monkeypatch.setattr(knowledge, "MAX_INDEX_BYTES", size)
    msg = knowledge.rebuild_knowledge_base()
    assert "indexed" in msg
    assert index_file.read_bytes() == original

    # One byte over: refused, and the previously valid index survives untouched.
    monkeypatch.setattr(knowledge, "MAX_INDEX_BYTES", size - 1)
    msg = knowledge.rebuild_knowledge_base()
    assert "too large" in msg.lower()
    assert index_file.read_bytes() == original


def test_knowledge_index_serialization_is_bounded(monkeypatch, tmp_path):
    docs = _point_docs_at(monkeypatch, tmp_path)
    (docs / "a.md").write_text("dijkstra shortest path " * 10_000)
    real_dumps = json.dumps
    seen = []

    def spy(obj, *args, **kwargs):
        text = real_dumps(obj, *args, **kwargs)
        seen.append(text)
        return text

    monkeypatch.setattr(knowledge.json, "dumps", spy)

    knowledge.rebuild_knowledge_base()

    assert seen, "index serialization happened"
    largest_step = max(len(s.encode("utf-8")) for s in seen)
    # No single serialization step may materialize the whole document: each call
    # handles at most one entry (~1.5 KiB here), never the ~280 KiB full index.
    assert largest_step <= 64 * 1024

    # The on-disk format is unchanged: the file is exactly json.dumps of its content.
    text = _index_path(tmp_path).read_text(encoding="utf-8")
    saved = json.loads(text)
    assert text == real_dumps({"entries": saved["entries"]})


def test_knowledge_pdf_extraction_stops_at_text_cap(monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []

    class CountingPage:
        def __init__(self, size):
            self.size = size

        def extract_text(self):
            calls.append(1)
            return "p" * self.size

    class CountingReader:
        def __init__(self, handle):
            self.pages = [CountingPage(10_000) for _ in range(5)]

    monkeypatch.setattr(knowledge, "_pdf_reader_class", lambda: CountingReader)
    monkeypatch.setattr(knowledge, "MAX_PDF_PAGES", 5)
    monkeypatch.setattr(knowledge, "MAX_EXTRACTED_CHARS", 15_000)

    text = knowledge._load_pdf(str(pdf))

    # Exactly the cap (10,000 + one separator + 4,999), and extraction stopped
    # early: pages after the budget is spent are never parsed at all.
    assert len(text) == 15_000
    assert len(calls) == 2
    assert text.startswith("p" * 10_000)


def test_knowledge_pdf_read_error_is_contained(monkeypatch, tmp_path):
    pdf = tmp_path / "bad.pdf"
    pdf.write_bytes(b"not a pdf")

    class BoomReader:
        def __init__(self, handle):
            raise ValueError("boom")

    monkeypatch.setattr(knowledge, "_pdf_reader_class", lambda: BoomReader)

    assert knowledge._load_pdf(str(pdf)) == ""
