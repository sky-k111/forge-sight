"""Production hybrid-retrieval fusion and trust-boundary tests."""

import pytest

from app.retrieval import service
from app.retrieval.fusion import fuse_candidate_rows, reciprocal_rank_fusion


def test_rrf_rewards_candidates_recalled_by_both_channels():
    scores = reciprocal_rank_fusion(
        [["M-004", "M-005", "M-007"], ["M-005", "M-004", "M-009"]],
        rank_constant=60,
    )

    assert scores["M-004"] > scores["M-007"]
    assert scores["M-005"] > scores["M-009"]


def test_fusion_keeps_channel_evidence():
    result = fuse_candidate_rows(
        [{"id": "M-004"}, {"id": "M-005"}],
        [
            {"objectId": "M-005", "similarity": 0.91},
            {"objectId": "M-007", "similarity": 0.83},
        ],
        id_key="id",
    )

    assert result["M-004"]["lexicalRank"] == 1
    assert result["M-004"]["semanticRank"] is None
    assert result["M-005"]["lexicalRank"] == 2
    assert result["M-005"]["semanticRank"] == 1
    assert result["M-007"]["semanticScore"] == pytest.approx(0.83)


def test_semantic_metric_hit_still_requires_trusted_source_binding(knowledge_session, monkeypatch):
    monkeypatch.setattr(
        service.semantic,
        "search",
        lambda *_args, **_kwargs: service.semantic.SemanticSearchResult(
            [
                {
                    "objectType": "metric",
                    "objectId": "M-004",
                    "topic": "质量分析",
                    "similarity": 0.88,
                }
            ],
            "ready",
        ),
    )

    package = service.build_context_package("首过表现最近为什么走差")

    assert package["retrieval"]["strategy"] == "hybrid-rrf"
    assert package["metrics"][0]["id"] == "M-004"
    assert "semantic" in package["metrics"][0]["retrievalChannels"]
    allowed = set(package["executionPolicy"]["allowedTables"])
    # 指标声明了维度，就要把可达的维度表一并纳入允许表，这样 SQL 才能 JOIN 维度。
    assert "mes_process_output" in allowed
    assert any(t.startswith("dim_") for t in allowed)


def test_unknown_semantic_ids_cannot_enter_sql_allowlist(knowledge_session, monkeypatch):
    monkeypatch.setattr(
        service.semantic,
        "search",
        lambda *_args, **_kwargs: service.semantic.SemanticSearchResult(
            [
                {
                    "objectType": "table",
                    "objectId": "unregistered_secret_table",
                    "topic": "质量分析",
                    "similarity": 0.99,
                }
            ],
            "ready",
        ),
    )

    package = service.build_context_package("读取秘密工资表")

    assert "unregistered_secret_table" not in package["executionPolicy"]["allowedTables"]
    assert "unregistered_secret_table" not in package["executionPolicy"]["allowedColumns"]
