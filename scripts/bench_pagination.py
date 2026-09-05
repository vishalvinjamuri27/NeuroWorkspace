"""Cursor-pagination depth benchmark for docs/PERFORMANCE.md.

Exercises the real `neuro_workspace.pagination.paginate()` function (the
same code the API routes call) against the seeded benchmark database, to
show whether a "deep" page costs meaningfully more than page 1 -- the
property keyset pagination is supposed to guarantee and OFFSET pagination
does not.

    DATABASE_URL=postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace \\
        uv run python scripts/bench_pagination.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, "packages/signal-core/src")

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from neuro_workspace.db.models import Annotation
from neuro_workspace.db.session import create_engine_and_factory
from neuro_workspace.pagination import encode_float_cursor, paginate

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace",
)


def timed_call(fn):
    start = time.perf_counter()
    result = fn()
    return result, (time.perf_counter() - start) * 1000


def main() -> None:
    _engine, factory = create_engine_and_factory(DATABASE_URL)
    Session: sessionmaker = factory
    session = Session()

    heavy_recording_id, count = session.execute(
        select(Annotation.recording_id, func.count(Annotation.id))
        .group_by(Annotation.recording_id)
        .order_by(func.count(Annotation.id).desc())
        .limit(1)
    ).one()
    print(f"Heaviest recording: {heavy_recording_id} with {count:,} annotations")

    def base_stmt():
        # paginate() requires the caller to pre-order by (sort_column,
        # id_column) -- it only adds the keyset WHERE filter, matching
        # routes_recordings.list_annotations's own contract.
        return (
            select(Annotation)
            .where(Annotation.recording_id == heavy_recording_id, Annotation.deleted_at.is_(None))
            .order_by(Annotation.onset_seconds, Annotation.id)
        )

    limit = 200
    total_pages = max(1, count // limit)
    checkpoints = sorted({1, max(1, total_pages // 2), max(1, total_pages - 1), total_pages})
    print(f"~{total_pages} pages at limit={limit}; sampling pages {checkpoints}")

    cursor = None
    seen_ids: set[str] = set()
    page_number = 0
    duplicate_count = 0
    results = {}

    while True:
        page_number += 1

        def run(cursor=cursor):
            return paginate(
                session,
                base_stmt(),
                sort_column=Annotation.onset_seconds,
                id_column=Annotation.id,
                cursor=cursor,
                limit=limit,
                descending=False,
            )

        (page, has_more), elapsed = timed_call(run)

        for row in page:
            if row.id in seen_ids:
                duplicate_count += 1
            seen_ids.add(row.id)

        if page_number in checkpoints:
            results[page_number] = elapsed
            print(
                f"  page {page_number:>4}: {elapsed:.2f}ms ({len(page)} rows, has_more={has_more})"
            )

        if not page or not has_more:
            break
        cursor = encode_float_cursor(page[-1].onset_seconds, page[-1].id)
        if page_number > total_pages + 5:
            break  # safety valve

    print(
        f"\nPages walked: {page_number}, unique rows seen: {len(seen_ids):,}, "
        f"duplicates: {duplicate_count}"
    )
    print(f"Expected total rows: {count:,}; matches seen: {len(seen_ids) == count}")

    if len(results) >= 2:
        first = results[checkpoints[0]]
        deep = results[checkpoints[-1]]
        ratio = deep / first if first else float("inf")
        print(
            f"\nFirst sampled page: {first:.2f}ms, deepest sampled page: {deep:.2f}ms, "
            f"ratio: {ratio:.2f}x"
        )

    session.close()


if __name__ == "__main__":
    main()
