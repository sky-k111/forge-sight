"""Hybrid retrieval rank fusion utilities.

Scores from lexical rules and vector similarity are not numerically comparable.
RRF combines their ranks instead, avoiding model-specific score calibration.
"""

from __future__ import annotations

from collections.abc import Iterable


def reciprocal_rank_fusion(
    rankings: Iterable[Iterable[str]],
    *,
    rank_constant: int = 60,
    weights: Iterable[float] | None = None,
) -> dict[str, float]:
    """Return stable RRF scores keyed by candidate id.

    Duplicate ids inside one ranking are ignored. ``rank_constant`` follows the
    common RRF default of 60 and must stay positive.
    """

    rank_constant = max(1, int(rank_constant))
    ranking_list = [list(ranking) for ranking in rankings]
    weight_list = list(weights) if weights is not None else [1.0] * len(ranking_list)
    if len(weight_list) != len(ranking_list):
        raise ValueError("weights length must match rankings length")

    scores: dict[str, float] = {}
    for ranking, weight in zip(ranking_list, weight_list, strict=True):
        seen: set[str] = set()
        for rank, candidate_id in enumerate(ranking, start=1):
            key = str(candidate_id)
            if not key or key in seen:
                continue
            seen.add(key)
            scores[key] = scores.get(key, 0.0) + float(weight) / (rank_constant + rank)
    return scores


def fuse_candidate_rows(
    lexical_rows: list[dict],
    semantic_hits: list[dict],
    *,
    id_key: str,
    rank_constant: int = 60,
) -> dict[str, dict]:
    """Expose fused and channel-specific ranks for observability and tests."""

    lexical_ids = [str(row[id_key]) for row in lexical_rows]
    semantic_ids = [str(row["objectId"]) for row in semantic_hits]
    fused = reciprocal_rank_fusion(
        [lexical_ids, semantic_ids],
        rank_constant=rank_constant,
    )
    lexical_rank = {candidate_id: rank for rank, candidate_id in enumerate(lexical_ids, start=1)}
    semantic_rank = {candidate_id: rank for rank, candidate_id in enumerate(semantic_ids, start=1)}
    semantic_score = {
        str(row["objectId"]): float(row.get("similarity", 0.0)) for row in semantic_hits
    }
    return {
        candidate_id: {
            "fusedScore": score,
            "lexicalRank": lexical_rank.get(candidate_id),
            "semanticRank": semantic_rank.get(candidate_id),
            "semanticScore": semantic_score.get(candidate_id),
        }
        for candidate_id, score in fused.items()
    }
