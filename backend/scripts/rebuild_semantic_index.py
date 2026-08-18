"""Rebuild the persistent semantic index for registered data sources."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Support the documented direct invocation from backend/:
# python scripts/rebuild_semantic_index.py --full
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.db.models import DataSource
from app.db.platform import PlatformSession
from app.retrieval.semantic import rebuild_source_index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument(
        "--full",
        action="store_true",
        help="Force re-embedding every document. Use after changing model or dimensions.",
    )
    args = parser.parse_args()

    with PlatformSession() as session:
        query = session.query(DataSource.id).order_by(DataSource.id)
        if args.source_id is not None:
            query = query.filter(DataSource.id == args.source_id)
        source_ids = [row[0] for row in query.all()]
    if not source_ids:
        raise SystemExit("没有可索引的数据源")

    summaries = []
    for source_id in source_ids:
        with PlatformSession() as session:
            summaries.append(
                rebuild_source_index(
                    session,
                    source_id,
                    batch_size=max(1, args.batch_size),
                    force=args.full,
                )
            )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
