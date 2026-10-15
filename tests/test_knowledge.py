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

def test_knowledge_rebuild_skips_symlinked_files_outside_docs_root(monkeypatch, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "inside.md").write_text("Dijkstra finds the shortest path.", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("OUTSIDE_PRIVATE_SENTINEL", encoding="utf-8")
    (docs / "external.txt").symlink_to(outside)
    index_file = tmp_path / "index.json"

    monkeypatch.setattr(knowledge, "DOCS_DIR", str(docs))
    monkeypatch.setattr(knowledge, "INDEX_FILE", str(index_file))
    monkeypatch.setattr(knowledge, "_get_model", lambda: _FakeEmbedModel())

    message = knowledge.rebuild_knowledge_base()
    assert "indexed 1 chunks from 1 files" in message
    saved = json.loads(index_file.read_text(encoding="utf-8"))
    assert all("OUTSIDE_PRIVATE_SENTINEL" not in entry["text"] for entry in saved["entries"])
    assert all(Path(entry["source"]).resolve().is_relative_to(docs.resolve()) for entry in saved["entries"])

