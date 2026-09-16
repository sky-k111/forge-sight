from app.knowledge.metric_semantics import metric_semantics


def test_composition_requires_a_provably_additive_binding():
    for formula in ("SUM(good_qty + defect_qty)", "COUNT(*)", "SUM(a) + SUM(b)"):
        assert metric_semantics(formula)["compositionAllowed"] is True
    for formula in ("SUM(good_qty) / NULLIF(SUM(total_qty), 0) * 100", "AVG(rate)", "COUNT(DISTINCT equipment_id)", "MAX(qty)", "SUM(qty) * 100", "unbound"):
        assert metric_semantics(formula)["compositionAllowed"] is False
    assert metric_semantics("SUM(qty)")["direction"] == "neutral"
