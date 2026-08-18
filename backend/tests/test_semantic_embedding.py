"""Embedding provider contract tests."""

from types import SimpleNamespace

from app.retrieval import semantic


def test_embedding_request_sends_configured_dimensions(monkeypatch):
    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[0.1, 0.2, 0.3])]
            )

    monkeypatch.setattr(
        semantic,
        "_embedding_client",
        lambda: SimpleNamespace(embeddings=FakeEmbeddings()),
    )
    monkeypatch.setattr(semantic.settings, "embedding_model", "text-embedding-v4")
    monkeypatch.setattr(semantic.settings, "embedding_dimensions", 3)
    monkeypatch.setattr(semantic.settings, "embedding_send_dimensions", True)

    vectors = semantic.embed_texts(["工序良率"])

    assert vectors == [[0.1, 0.2, 0.3]]
    assert captured == {
        "model": "text-embedding-v4",
        "input": ["工序良率"],
        "dimensions": 3,
    }


def test_embedding_request_can_omit_dimensions_for_other_providers(monkeypatch):
    captured = {}

    class FakeEmbeddings:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[0.1])])

    monkeypatch.setattr(
        semantic,
        "_embedding_client",
        lambda: SimpleNamespace(embeddings=FakeEmbeddings()),
    )
    monkeypatch.setattr(semantic.settings, "embedding_dimensions", 1)
    monkeypatch.setattr(semantic.settings, "embedding_send_dimensions", False)

    semantic.embed_texts(["设备停机"])

    assert "dimensions" not in captured
