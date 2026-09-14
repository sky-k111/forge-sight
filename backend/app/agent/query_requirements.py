"""Check requested dimensions against SQL projections and returned columns."""

import re

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import build_scope
from typing import Any

from app.agent.query_compiler import _DIM_KEYWORDS, _dim_time_grain, _is_time_dim, _pick_metric
from app.agent.validators import resolve_dimension_field, resolve_metric


def _select_metric(task, context):
    """Bind the accepted semantic metric; never reinterpret structured intent from prose."""
    candidates = [item for item in context.get("metrics", []) if item.get("physicalFormula")]
    semantic_intent = task.get("semanticIntent") or {}
    requested = list(semantic_intent.get("metrics") or [])
    existing = (task.get("queryBinding") or {}).get("metric") or {}
    requested_id = existing.get("id") or (requested[0].get("id") if requested else None)
    if requested_id:
        return next((item for item in candidates if str(item.get("id")) == str(requested_id)), {})

    requested_name = (
        (requested[0].get("semantic") if requested else None)
        or task.get("metricHint")
    )
    if requested_name:
        resolved = resolve_metric(
            str(requested_name),
            {"metrics": candidates},
            str(task.get("normalizedQuestion") or task.get("originalQuestion") or ""),
        )
        if resolved:
            return resolved
    if len(candidates) == 1:
        return candidates[0]
    # Legacy/fallback intents have no accepted semantic contract yet. Keep the
    # old lexical picker only at that compatibility boundary.
    if not semantic_intent:
        qtext = str(task.get("normalizedQuestion") or task.get("originalQuestion") or "")
        return _pick_metric(candidates, qtext, task.get("metricHint")) or {}
    return {}


def apply_resolved_intent(task: dict[str, Any], resolved: dict[str, Any] | None) -> dict[str, Any]:
    """Merge deterministic validator output into the current task contract.

    Validation is task-local: compound requests carry a different semantic
    contract for each child task.  Keeping this merge explicit prevents a
    successfully resolved metric id / physical dimension code from existing
    only in audit metadata while the compiler keeps using stale input.
    """
    semantic = dict(task.get("semanticIntent") or {})
    if not semantic or not resolved:
        return dict(task)
    if "metrics" in resolved:
        semantic["metrics"] = list(resolved.get("metrics") or [])
    if "dimensions" in resolved:
        semantic["dimensions"] = list(resolved.get("dimensions") or [])
    return {**task, "semanticIntent": semantic}


def bind_query_requirements(task, context):
    """Resolve semantic dimensions once; compiler and validator consume this binding."""
    metric = _select_metric(task, context)
    metric_tables = set(metric.get("sourceTables") or [])
    tables = list(context.get("tables") or [])

    def table_name(table):
        return str(table.get("qualifiedName") or table.get("name") or "")

    identities = {}
    bare_counts = {}
    for item in tables:
        canonical = table_name(item)
        identities[canonical] = canonical
        bare = str(item.get("name") or "")
        bare_counts[bare] = bare_counts.get(bare, 0) + 1
    for item in tables:
        bare = str(item.get("name") or "")
        if bare and bare_counts.get(bare) == 1:
            identities[bare] = table_name(item)

    def canonical_table(value):
        return identities.get(str(value or ""))

    fact = next((table for table in tables if {table_name(table), str(table.get("name") or "")} & metric_tables), None)
    if fact is None:
        fact = next((table for table in tables if "fact" in str(table.get("objectType") or "").lower()), None)

    def locate_field(field, *, prefer_fact=False):
        matches = [table for table in tables if any(str(item.get("name") or "").lower() == str(field or "").lower()
                                                    for item in table.get("fields") or [])]
        if prefer_fact and fact in matches:
            return table_name(fact), "bound"
        if len(matches) == 1:
            return table_name(matches[0]), "bound"
        if fact in matches:
            return table_name(fact), "bound"
        return None, "ambiguous" if matches else "unresolved"

    def resolve_semantic_field(value, *, exact_only=False):
        """Resolve a filter/dimension label without assuming it lives on the fact table."""
        needle = str(value or "").strip().lower()
        if not needle:
            return None
        exact = []
        fuzzy = []
        for table in tables:
            for item in table.get("fields") or []:
                name = str(item.get("name") or "")
                semantic = str(item.get("semanticName") or item.get("cnName") or "")
                if needle in {name.lower(), semantic.lower()}:
                    exact.append((table, name))
                elif semantic and (needle in semantic.lower() or semantic.lower() in needle):
                    fuzzy.append((table, name))
        candidates = exact if exact_only else (exact or fuzzy)
        if len(candidates) == 1:
            return candidates[0][1]
        fact_match = next((name for table, name in candidates if table is fact), None)
        return fact_match

    def value_shape(value):
        """Describe an example value without embedding business-specific IDs."""
        shaped = []
        for char in str(value or "").strip():
            if char.isascii() and char.isalpha():
                shaped.append("A")
            elif char.isdigit():
                shaped.append("9")
            elif "\u4e00" <= char <= "\u9fff":
                shaped.append("字")
            else:
                shaped.append(char)
        return "".join(shaped)

    def resolve_filter_field(semantic, value):
        """Choose ID/code/name columns from metadata samples and the filter value."""
        needle = str(semantic or "").strip().lower()
        candidates = []
        for table in tables:
            for item in table.get("fields") or []:
                name = str(item.get("name") or "")
                label = str(item.get("semanticName") or item.get("cnName") or "")
                if not name or not label or (needle not in label.lower() and needle != name.lower()):
                    continue
                score = 100 if needle in {name.lower(), label.lower()} else 50
                sample = item.get("sampleValue")
                if sample not in (None, "") and value_shape(sample) == value_shape(value):
                    score += 200
                if item.get("isPrimary"):
                    score += 10
                candidates.append((score, table, name))
        if not candidates:
            return None
        best = max(item[0] for item in candidates)
        winners = [(table, name) for score, table, name in candidates if score == best]
        return winners[0][1] if len(winners) == 1 else None

    fact_name = table_name(fact) if fact else None
    bound = []
    semantic_dimensions = {}
    for item in ((task.get("semanticIntent") or {}).get("dimensions") or []):
        for key in (item.get("semantic"), item.get("label"), item.get("code")):
            if key:
                semantic_dimensions[str(key)] = item
    for dimension in task.get("groupBy") or []:
        if dimension == "比较周期":
            # Virtual dimension rendered from the accepted comparison periods.
            # It is backed by the metric's time field but has no calendar grain.
            field = metric.get("physicalTimeField")
            grain = None
            table, status = locate_field(field, prefer_fact=True)
        elif _is_time_dim(dimension):
            field = metric.get("physicalTimeField")
            grain = _dim_time_grain(dimension) or (task.get("timeConstraint") or {}).get("timeGrain") or "day"
            table, status = locate_field(field, prefer_fact=True)
        else:
            spec = semantic_dimensions.get(str(dimension)) or {}
            field = spec.get("code") or resolve_semantic_field(str(dimension), exact_only=True)
            if not field and not task.get("semanticIntent"):
                field = resolve_dimension_field(str(dimension), context)
            grain = None
            table, status = locate_field(field, prefer_fact=True)
        bound.append({"semantic": dimension, "field": field, "table": table, "grain": grain,
                      "status": status if field else "unresolved"})
    filter_bound = []
    for condition in task.get("filters") or []:
        semantic = str(condition.get("semantic") or condition.get("field") or "")
        metric_aliases = metric.get("aliases") or []
        if isinstance(metric_aliases, str):
            metric_aliases = [metric_aliases]
        metric_rule_text = " ".join(str(value or "") for value in (
            metric.get("name"), metric.get("definition"), metric.get("semanticFormula"),
            *metric_aliases,
        ))
        expected_value = condition.get("value")
        operator = str(condition.get("operator") or "eq").lower()
        formula = str(metric.get("physicalFormula") or metric.get("semanticFormula") or "")
        operator_symbol = {"lt": "<", "lte": "<=", "gt": ">", "gte": ">="}.get(operator)
        # A published Boolean/business-rule metric already owns its predicate.
        # Mark an LLM-restated comparison as satisfied by that formula rather than
        # binding the prose ("库存 < 安全库存") to an arbitrary column.
        def normalize_rule_term(value):
            return re.sub(r"[\s_\-量数值]", "", str(value or "").lower())

        semantic_norm = normalize_rule_term(semantic)
        rule_norm = normalize_rule_term(metric_rule_text)
        satisfied_by_metric = bool(
            operator_symbol
            and operator_symbol in formula
            and semantic_norm and semantic_norm in rule_norm
            and isinstance(expected_value, str) and expected_value in metric_rule_text
        )
        if satisfied_by_metric:
            filter_bound.append({
                **condition,
                "semantic": semantic,
                "field": None,
                "table": fact_name,
                "status": "metric_formula",
                "satisfiedByMetric": metric.get("id") or metric.get("name"),
            })
            continue
        spec = semantic_dimensions.get(semantic) or {}
        field = resolve_filter_field(semantic, condition.get("value")) or spec.get("code") or resolve_semantic_field(semantic, exact_only=True)
        if not field and not task.get("semanticIntent"):
            field = resolve_semantic_field(semantic) or resolve_dimension_field(semantic, context)
        table, status = locate_field(field, prefer_fact=True)
        filter_bound.append({
            **condition,
            "semantic": semantic,
            "field": field,
            "table": table,
            "status": status if field else "unresolved",
        })
    graph: dict[str, list[dict[str, Any]]] = {}
    for relation in context.get("relations") or []:
        from_table = canonical_table(relation.get("fromQualifiedTable") or relation.get("fromTable"))
        to_table = canonical_table(relation.get("toQualifiedTable") or relation.get("toTable"))
        if not from_table or not to_table:
            continue
        graph.setdefault(from_table, []).append({
            "fromTable": from_table, "fromField": relation.get("fromField"),
            "toTable": to_table, "toField": relation.get("toField"),
        })
        graph.setdefault(to_table, []).append({
            "fromTable": to_table, "fromField": relation.get("toField"),
            "toTable": from_table, "toField": relation.get("fromField"),
        })

    def shortest_path(target):
        if not fact_name or not target:
            return None
        if target == fact_name:
            return []
        queue = [(fact_name, [])]
        visited = {fact_name}
        while queue:
            current, path = queue.pop(0)
            for edge in graph.get(current, []):
                nxt = edge["toTable"]
                if nxt in visited:
                    continue
                next_path = [*path, edge]
                if nxt == target:
                    return next_path
                visited.add(nxt)
                queue.append((nxt, next_path))
        return None

    joins = []
    joined_tables = {fact_name} if fact_name else set()
    for binding in [*bound, *filter_bound]:
        target = binding.get("table")
        path = shortest_path(target)
        if path is None:
            if target and target != fact_name:
                binding["status"] = "unresolved_join"
            continue
        for edge in path:
            if edge["toTable"] in joined_tables:
                continue
            joins.append({**edge, "alias": f"d{len(joins) + 1}"})
            joined_tables.add(edge["toTable"])
    aliases = {fact_name: "f"} if fact_name else {}
    aliases.update({item["toTable"]: item["alias"] for item in joins})
    for binding in [*bound, *filter_bound]:
        binding["alias"] = aliases.get(binding.get("table"))

    contract = {
        "version": 1,
        "metric": metric or None,
        "factTable": fact_name,
        "dimensions": bound,
        "filters": filter_bound,
        "time": next((item for item in bound if item.get("grain")), None),
        "joins": joins,
    }
    return {
        **task,
        "dimensionBindings": bound,
        "filterBindings": filter_bound,
        "queryBinding": contract,
    }


def _detected_time_grain(node, dialect):
    def unit_value(expression):
        unit = expression.args.get("unit")
        raw = str(getattr(unit, "this", unit) or "").strip("'\"").lower()
        return {
            "iw": "week", "wk": "week", "ww": "week",
            "mm": "month", "mon": "month",
            "q": "quarter", "qq": "quarter",
            "yyyy": "year", "yy": "year",
            "dd": "day",
        }.get(raw, raw)

    # Prefer SQLGlot's normalized AST.  This survives aliases, whitespace and
    # dialect spellings much better than textual keyword matching.
    date_parts = list(node.find_all(exp.DateFromParts)) if hasattr(exp, "DateFromParts") else []
    if date_parts:
        return "month" if any(part.args.get("month") and not isinstance(part.args.get("month"), exp.Literal)
                              for part in date_parts) else "year"
    for cls_name in ("TimestampTrunc", "DateTrunc", "DateAdd", "DateDiff"):
        cls = getattr(exp, cls_name, None)
        if cls:
            for expression in node.find_all(cls):
                grain = unit_value(expression)
                if grain in {"year", "quarter", "month", "week", "day"}:
                    return grain
    quarter_cls = getattr(exp, "Quarter", None)
    if quarter_cls and node.find(quarter_cls):
        return "quarter"
    for expression in node.find_all(exp.TimeToStr):
        fmt = str(getattr(expression.args.get("format"), "this", "") or "").lower()
        if any(token in fmt for token in ("%v", "%u", "%w")):
            return "week"
        if "%m" in fmt and "%d" in fmt:
            return "day"
        if "%m" in fmt:
            return "month"
        if "%y" in fmt:
            return "year"
    for cast in node.find_all(exp.Cast):
        if str(cast.args.get("to") or "").upper() == "DATE":
            return "day"
    text = node.sql(dialect=dialect).lower().replace(" ", "")
    if "trunc(" in text:
        if any(token in text for token in (",'iw')", ',"iw")')):
            return "week"
        if any(token in text for token in (",'q')", ',"q")')):
            return "quarter"
        if any(token in text for token in (",'mm')", ',"mm")')):
            return "month"
        if any(token in text for token in (",'yyyy')", ',"yyyy")')):
            return "year"
        if "," not in text:
            return "day"
    if any(token in text for token in ("yearweek(", "date_trunc('week'", 'date_trunc("week"',
                                             "datepart(week", "datepart(iso_week", "'iw'", "%y-%u", "%x-%v", "%w")):
        return "week"
    if any(token in text for token in ("date_trunc('quarter'", 'date_trunc("quarter"', "quarter(")):
        return "quarter"
    if any(token in text for token in ("date(", "date_trunc('day'", 'date_trunc("day"', "%y-%m-%d")):
        return "day"
    if any(token in text for token in ("%y-%m", "yyyy-mm", "date_trunc('month'", 'date_trunc("month"',
                                             "datepart(month", "month(")):
        return "month"
    if any(token in text for token in ("date_trunc('year'", 'date_trunc("year"', "datepart(year", "year(", "%y")):
        return "year"
    return None


def dimension_outputs(sql, task, context):
    dialect = (context.get("source") or {}).get("type", "mysql")
    dialect = {"postgresql": "postgres", "sqlserver": "tsql"}.get(dialect, dialect)
    try:
        root = sqlglot.parse_one(sql, read=dialect)
        scope = build_scope(root)
        if scope is None or not isinstance(scope.expression, exp.Select):
            return {}, ["暂无法验证该查询结构的分组要求"]
    except (ValueError, sqlglot.errors.SqlglotError):
        return {}, ["无法解析查询以校验分析要求"]

    def lineage(node, current, seen=None):
        seen = set(seen or ())
        result = set()
        for col in node.find_all(exp.Column):
            candidates = ([current.sources.get(col.table)] if col.table else list(current.sources.values()))
            resolved = False
            for source in candidates:
                if not hasattr(source, "expression") or isinstance(source, exp.Table):
                    continue
                mark = (id(source), col.name)
                if mark in seen:
                    continue
                for projection in getattr(source.expression, "expressions", []):
                    if projection.alias_or_name.lower() == col.name.lower():
                        result |= lineage(projection, source, seen | {mark})
                        resolved = True
            if not resolved:
                result.add(col.name.lower())
        return result

    projections = scope.expression.expressions
    aliases = {p.alias_or_name.lower(): p for p in projections}
    group = scope.expression.args.get("group")
    group_fields = set()
    for node in group.expressions if group else []:
        if isinstance(node, exp.Literal) and node.is_int and 0 < int(node.this) <= len(projections):
            node = projections[int(node.this) - 1]
        elif isinstance(node, exp.Column) and not node.table:
            node = aliases.get(node.name.lower(), node)
        group_fields |= lineage(node, scope)
    outputs, failures = {}, []
    bindings = {str(item.get("semantic")): item for item in task.get("dimensionBindings") or []}
    for dimension in task.get("groupBy") or []:
        keywords = (dimension, *_DIM_KEYWORDS.get(dimension, ()))
        fields = set()
        binding = bindings.get(str(dimension)) or {}
        if binding.get("field"):
            fields.add(str(binding["field"]).lower())
        elif _is_time_dim(dimension):
            fields |= {m["physicalTimeField"].lower() for m in context.get("metrics", []) if m.get("physicalTimeField")}
        if not fields:  # legacy validation only; a bound contract is authoritative
            for table in context.get("tables", []):
                for field in table.get("fields", []):
                    name = str(field.get("name") or "")
                    semantic = str(field.get("semanticName") or "")
                    if name and any(k.lower() in (name + " " + semantic).lower() for k in keywords):
                        fields.add(name.lower())
        if not fields:
            failures.append(f"当前数据源没有可绑定的{dimension}字段")
            continue
        matches = []
        for projection in projections:
            # SUM(workshop_id) is not a workshop dimension.
            if any(projection.find_all(exp.AggFunc)):
                continue
            if lineage(projection, scope) & fields:
                matches.append(projection.alias_or_name)
        if not matches:
            failures.append(f"查询缺少{dimension}结果列")
        elif group and not (group_fields & fields):
            failures.append(f"查询缺少{dimension}分组")
        elif not group and any(p.find(exp.AggFunc) for p in projections):
            failures.append(f"聚合查询缺少{dimension}分组")
        else:
            outputs[dimension] = matches
            expected_grain = binding.get("grain") or (_dim_time_grain(dimension) if _is_time_dim(dimension) else None)
            if expected_grain:
                actual = {_detected_time_grain(p, dialect) for p in projections if p.alias_or_name in matches}
                actual.discard(None)
                if expected_grain not in actual:
                    failures.append(f"查询要求按{dimension}分桶，实际时间粒度不是{expected_grain}")
    return outputs, failures


def query_contract_failures(sql, task, context):
    """Validate bound filters, ordering and row limits from the SQL AST.

    This deliberately validates the physical contract instead of looking for
    business words in SQL text. It therefore applies equally to deterministic
    and model-generated queries.
    """
    dialect = (context.get("source") or {}).get("type", "mysql")
    dialect = {"postgresql": "postgres", "sqlserver": "tsql"}.get(dialect, dialect)
    try:
        root = sqlglot.parse_one(sql, read=dialect)
    except (ValueError, sqlglot.errors.SqlglotError):
        return ["无法解析查询以校验筛选、排序和数量限制"]

    failures = []
    where = root.args.get("where")

    # Reject every unrequested physical grouping generically. This replaces
    # one-off guards such as “do not add product_name to a line chart”.
    requested_bindings = task.get("dimensionBindings") or []
    allowed_group_fields = {
        str(item.get("field") or "").lower() for item in requested_bindings if item.get("field")
    }
    # A requested conformed dimension may be represented by several descriptive
    # attributes from the same joined dimension table (ID/code/name/model).  They
    # do not introduce a new analytical grouping grain.  Keep rejecting extra
    # fields from the fact table or unrelated dimensions.
    fact_table = str((task.get("queryBinding") or {}).get("factTable") or "")
    requested_dimension_tables = {
        str(item.get("table") or "") for item in requested_bindings
        if item.get("table") and str(item.get("table")) != fact_table and not item.get("grain")
    }
    for table in context.get("tables") or []:
        identities = {str(table.get("qualifiedName") or ""), str(table.get("name") or "")}
        if requested_dimension_tables & identities:
            allowed_group_fields.update(
                str(field.get("name") or "").lower()
                for field in table.get("fields") or [] if field.get("name")
            )
    allowed_group_aliases = {
        str(item.get("semantic") or "").lower() for item in requested_bindings if item.get("semantic")
    }
    group = root.args.get("group")
    if group is not None and allowed_group_fields:
        projections = list(root.expressions)
        for expression in group.expressions:
            if isinstance(expression, exp.Literal) and expression.is_int:
                index = int(expression.this) - 1
                expression = projections[index] if 0 <= index < len(projections) else expression
            columns = list(expression.find_all(exp.Column))
            if isinstance(expression, exp.Column):
                columns = [expression]
            for column in columns:
                name = column.name.lower()
                if name not in allowed_group_fields and name not in allowed_group_aliases:
                    failures.append(f"查询增加了未请求的分组字段「{column.name}」")

    def column_matches(node, binding):
        if not isinstance(node, exp.Column):
            return False
        if node.name.lower() != str(binding.get("field") or "").lower():
            return False
        alias = str(binding.get("alias") or "").lower()
        return not alias or not node.table or node.table.lower() == alias

    def scalar(node):
        if isinstance(node, exp.Null):
            return None
        if isinstance(node, exp.Boolean):
            return str(node.this).lower() == "true"
        if isinstance(node, exp.Literal):
            return str(node.this)
        return str(node.sql(dialect=dialect)).strip("'\"") if node is not None else None

    def same_value(actual, expected):
        if actual is None or expected is None:
            return actual is expected
        return str(actual) == str(expected)

    binary_nodes = {
        "eq": exp.EQ, "ne": exp.NEQ, "gt": exp.GT,
        "gte": exp.GTE, "lt": exp.LT, "lte": exp.LTE,
    }
    operator_aliases = {
        "=": "eq", "==": "eq", "!=": "ne", "<>": "ne",
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
    }
    for binding in (task.get("queryBinding") or {}).get("filters") or task.get("filterBindings") or []:
        semantic = binding.get("semantic") or binding.get("field") or "筛选条件"
        if binding.get("status") == "metric_formula":
            continue
        if binding.get("status") != "bound" or not binding.get("field"):
            failures.append(f"筛选条件「{semantic}」没有唯一绑定到物理字段")
            continue
        if where is None:
            failures.append(f"查询缺少筛选条件「{semantic}」")
            continue
        operator = str(binding.get("operator") or "eq").strip().lower()
        operator = operator_aliases.get(operator, operator)
        expected = binding.get("value")
        matched = False
        if operator in {"eq", "ne"} and expected is None:
            for node in where.find_all(exp.Is):
                is_negated = isinstance(node.parent, exp.Not)
                if column_matches(node.this, binding) and isinstance(node.expression, exp.Null) \
                        and is_negated == (operator == "ne"):
                    matched = True
                    break
        elif operator in binary_nodes:
            for node in where.find_all(binary_nodes[operator]):
                if column_matches(node.this, binding) and same_value(scalar(node.expression), expected):
                    matched = True
                    break
                if operator in {"eq", "ne"} and column_matches(node.expression, binding) and same_value(scalar(node.this), expected):
                    matched = True
                    break
        elif operator in {"in", "not_in"} and isinstance(expected, list):
            for node in where.find_all(exp.In):
                is_negated = isinstance(node.parent, exp.Not)
                values = [scalar(item) for item in node.expressions]
                if column_matches(node.this, binding) and is_negated == (operator == "not_in") \
                        and len(values) == len(expected) and all(any(same_value(v, e) for v in values) for e in expected):
                    matched = True
                    break
        elif operator == "between" and isinstance(expected, (list, tuple)) and len(expected) == 2:
            for node in where.find_all(exp.Between):
                if column_matches(node.this, binding) and same_value(scalar(node.args.get("low")), expected[0]) \
                        and same_value(scalar(node.args.get("high")), expected[1]):
                    matched = True
                    break
        if not matched:
            failures.append(f"查询没有落实筛选条件「{semantic}」的字段、运算符和值")

    order = root.args.get("order")
    order_items = list(order.expressions) if order else []
    dimension_bindings = (task.get("queryBinding") or {}).get("dimensions") or task.get("dimensionBindings") or []
    metric = (task.get("queryBinding") or {}).get("metric") or {}
    for spec in task.get("sort") or []:
        requested = str(spec.get("field") or "")
        direction = str(spec.get("direction") or "asc").lower()
        names = {requested.lower()}
        names.update(
            str(value).lower()
            for binding in dimension_bindings
            if requested in {str(binding.get("semantic") or ""), str(binding.get("field") or "")}
            for value in (binding.get("semantic"), binding.get("field"))
            if value
        )
        metric_names = [str(metric.get("name") or ""), *(str(item) for item in metric.get("aliases") or [])]
        if any(requested == item or requested in item or item in requested for item in metric_names if item):
            names.update(item.lower() for item in metric_names if item)
        found = False
        for item in order_items:
            expression = item.this
            name = expression.name.lower() if isinstance(expression, exp.Column) else expression.alias_or_name.lower()
            actual_direction = "desc" if item.args.get("desc") else "asc"
            if name in names and actual_direction == direction:
                found = True
                break
        if not found:
            failures.append(f"查询没有落实排序「{requested} {direction.upper()}」")

    requested_limit = task.get("topN")
    if requested_limit:
        limit = root.args.get("limit")
        actual_limit = scalar(
            limit.expression if isinstance(limit, exp.Limit) else limit.args.get("count") if limit is not None else None
        )
        if str(actual_limit) != str(requested_limit):
            failures.append(f"查询没有落实 Top {requested_limit} 数量限制")
    return failures


def bound_result_schema(sql, task, context):
    """Describe result roles from the accepted bindings and SQL projections."""
    dialect = (context.get("source") or {}).get("type", "mysql")
    dialect = {"postgresql": "postgres", "sqlserver": "tsql"}.get(dialect, dialect)
    try:
        root = sqlglot.parse_one(sql, read=dialect)
        scope = build_scope(root)
        if scope is None or not isinstance(scope.expression, exp.Select):
            return []
    except (ValueError, sqlglot.errors.SqlglotError):
        return []

    outputs, failures = dimension_outputs(sql, task, context)
    if failures:
        return []
    bindings = {str(item.get("semantic")): item for item in task.get("dimensionBindings") or []}
    schema = []
    dimension_aliases = set()
    for semantic in task.get("groupBy") or []:
        binding = bindings.get(str(semantic)) or {}
        for alias in outputs.get(semantic, []):
            dimension_aliases.add(str(alias).lower())
            schema.append({
                "key": alias,
                "role": "time" if binding.get("grain") or _is_time_dim(str(semantic)) else "dimension",
                "grain": binding.get("grain"),
                "semantic": semantic,
            })
    metric = (task.get("queryBinding") or {}).get("metric") or {}
    for projection in scope.expression.expressions:
        alias = projection.alias_or_name
        if not alias or alias.lower() in dimension_aliases:
            continue
        if projection.find(exp.AggFunc):
            schema.append({
                "key": alias,
                "role": "measure",
                "metricId": metric.get("id"),
                "semantic": metric.get("name") or task.get("metricHint"),
                "unit": metric.get("unit"),
            })
    return schema


def validate_result(sql, task, context, execution):
    outputs, failures = dimension_outputs(sql, task, context)
    columns = {str(c).lower() for c in execution.get("columns", [])}
    for dim, aliases in outputs.items():
        if not any(a.lower() in columns for a in aliases):
            failures.append(f"返回结果缺少{dim}列，无法完成拆分")
    return failures
