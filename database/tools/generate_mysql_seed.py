"""Generate a portable MySQL seed file from the official CSV dataset."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


TABLES = [
    "dim_product",
    "dim_process",
    "dim_production_line",
    "dim_equipment",
    "mes_work_order",
    "mes_process_output",
    "qms_inspection",
    "qms_defect_detail",
    "eqp_downtime_record",
    "inv_inventory_snapshot",
]

BOOLEAN_COLUMNS = {
    ("dim_product", "is_active"),
    ("dim_process", "is_key_process"),
    ("eqp_downtime_record", "is_planned"),
}


def sql_literal(table: str, column: str, value: str) -> str:
    value = value.strip()
    if value == "":
        return "NULL"
    if (table, column) in BOOLEAN_COLUMNS:
        return "1" if value.lower() in {"1", "true", "yes", "y"} else "0"
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("'", "''")
    )
    return f"'{escaped}'"


def render_table(data_dir: Path, table: str, batch_size: int) -> list[str]:
    path = data_dir / f"{table}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Missing official dataset file: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = reader.fieldnames or []
        rows = list(reader)
    if not columns:
        raise ValueError(f"CSV has no header: {path}")

    quoted_columns = ", ".join(f"`{column}`" for column in columns)
    update_columns = columns[1:]
    update_clause = ", ".join(f"`{column}`=VALUES(`{column}`)" for column in update_columns)
    statements: list[str] = [f"-- {table}: {len(rows)} rows"]
    for offset in range(0, len(rows), batch_size):
        values = []
        for row in rows[offset : offset + batch_size]:
            values.append(
                "(" + ", ".join(sql_literal(table, column, row[column]) for column in columns) + ")"
            )
        statements.append(
            f"INSERT INTO `{table}` ({quoted_columns}) VALUES\n"
            + ",\n".join(values)
            + f"\nON DUPLICATE KEY UPDATE {update_clause};"
        )
    return statements


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True, help="Directory containing official CSV files")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=250)
    args = parser.parse_args()

    lines = [
        "-- Generated from the official ForgeSight competition dataset.",
        "-- Re-run database/tools/generate_mysql_seed.py when the CSV source changes.",
        "USE forge_sight;",
        "SET NAMES utf8mb4;",
        "SET FOREIGN_KEY_CHECKS = 0;",
        "",
    ]
    for table in TABLES:
        lines.extend(render_table(args.data_dir.resolve(), table, args.batch_size))
        lines.append("")
    lines.extend(["SET FOREIGN_KEY_CHECKS = 1;", ""])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"Generated {args.output}")


if __name__ == "__main__":
    main()
