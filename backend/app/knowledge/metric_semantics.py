"""Conservative visualization capabilities derived from the verified SQL binding.

Unknown formulas stay usable in tables/trends, but cannot claim a part-to-whole
relationship. Preference direction requires explicit governance, never a name guess.
"""
import sqlglot
from sqlglot import exp


def metric_semantics(formula: str, dialect: str = "mysql") -> dict:
    def additive(node):
        if isinstance(node, exp.Paren):
            return additive(node.this)
        if isinstance(node, (exp.Add, exp.Sub)):
            return additive(node.this) and additive(node.expression)
        if isinstance(node, (exp.Sum, exp.Count)):
            return not any(isinstance(child, (exp.Distinct, exp.Window, exp.Subquery, exp.AggFunc)) for child in node.this.walk())
        return False

    try:
        tree = sqlglot.parse_one(formula, read=dialect)
        allowed = additive(tree)
    except Exception:
        allowed = False
    return {
        "additive": allowed,
        "compositionAllowed": allowed,
        "direction": "neutral",
        "basis": "verified-binding-ast",
        "message": "可比较同一维度的构成；仅非负且互斥分类可展示占比" if allowed else "该指标不能证明可加总，不展示占比或累计贡献",
    }
