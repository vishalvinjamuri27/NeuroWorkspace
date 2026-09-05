"""EXPLAIN (ANALYZE, BUFFERS) + timing harness for docs/PERFORMANCE.md.

Run against the seeded benchmark Postgres (scripts/seed_benchmark.py). Not a
correctness test -- a reporting tool whose stdout is read and transcribed
(not paraphrased) into docs/PERFORMANCE.md.

    DATABASE_URL=postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace \\
        uv run python scripts/bench_queries.py
"""

from __future__ import annotations

import os
import statistics
import time

import psycopg

DATABASE_DSN = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace",
).replace("postgresql+psycopg://", "postgresql://")


def explain(conn: psycopg.Connection, label: str, sql: str, params: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT TEXT) {sql}", params)
        rows = cur.fetchall()
    print(f"\n--- {label} ---")
    print(f"SQL: {sql.strip()}")
    print(f"params: {params}")
    for (line,) in rows:
        print(line)


def time_query(
    conn: psycopg.Connection, label: str, sql: str, params: dict, reps: int = 30
) -> None:
    samples = []
    with conn.cursor() as cur:
        for _ in range(reps):
            start = time.perf_counter()
            cur.execute(sql, params)
            cur.fetchall()
            samples.append((time.perf_counter() - start) * 1000)
    samples.sort()

    def pct(p: float) -> float:
        idx = min(len(samples) - 1, int(len(samples) * p))
        return samples[idx]

    print(
        f"  {label}: n={len(samples)} "
        f"p50={pct(0.50):.2f}ms p95={pct(0.95):.2f}ms p99={pct(0.99):.2f}ms "
        f"min={min(samples):.2f}ms max={max(samples):.2f}ms "
        f"mean={statistics.mean(samples):.2f}ms"
    )


def pick_samples(conn: psycopg.Connection) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "select id, workspace_id from recordings order by created_at desc limit 1 offset 0"
        )
        recent_recording = cur.fetchone()

        cur.execute(
            """
            select r.id, count(a.id) as n
            from recordings r join annotations a on a.recording_id = r.id
            group by r.id order by n desc limit 1
            """
        )
        heavy_annotation_recording = cur.fetchone()

        cur.execute(
            """
            select r.id, count(a.id) as n
            from recordings r left join annotations a on a.recording_id = r.id
            group by r.id having count(a.id) between 1 and 5 limit 1
            """
        )
        light_annotation_recording = cur.fetchone()

        cur.execute("select id from projects order by random() limit 1")
        project_id = cur.fetchone()[0]

        cur.execute("select workspace_id from projects where id = %s", (project_id,))
        workspace_id = cur.fetchone()[0]

        cur.execute(
            "select user_id from memberships where workspace_id = %s limit 1", (workspace_id,)
        )
        user_row = cur.fetchone()
        user_id = user_row[0] if user_row else None

        cur.execute("select id from jobs where status = 'QUEUED' limit 1")
        row = cur.fetchone()
        queued_job_id = row[0] if row else None

        cur.execute("select version_id from qc_metrics limit 1")
        version_id = cur.fetchone()[0]

        cur.execute("select id, workspace_id from recordings limit 1")
        any_recording = cur.fetchone()

    return {
        "recent_recording_id": recent_recording[0] if recent_recording else None,
        "heavy_annotation_recording_id": heavy_annotation_recording[0]
        if heavy_annotation_recording
        else None,
        "heavy_annotation_count": heavy_annotation_recording[1]
        if heavy_annotation_recording
        else 0,
        "light_annotation_recording_id": light_annotation_recording[0]
        if light_annotation_recording
        else None,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "user_id": user_id,
        "queued_job_id": queued_job_id,
        "version_id": version_id,
        "any_recording_id": any_recording[0] if any_recording else None,
    }


def main() -> None:
    conn = psycopg.connect(DATABASE_DSN, autocommit=True)
    samples = pick_samples(conn)
    print("Sample ids used for this run:")
    for k, v in samples.items():
        print(f"  {k}: {v}")

    queries = [
        (
            "workspaces for current user (workspace_query_for_user)",
            """
            select w.* from workspaces w
            join memberships m on m.workspace_id = w.id
            where m.user_id = %(user_id)s and m.is_active = true
            order by w.created_at, w.id
            """,
        ),
        (
            "projects by workspace",
            "select * from projects where workspace_id = %(workspace_id)s order by created_at, id",
        ),
        (
            "recordings cursor page 1 (project_for_user + list_recordings, first page)",
            """
            select * from recordings
            where workspace_id = %(workspace_id)s and project_id = %(project_id)s
              and deleted_at is null
            order by created_at desc, id desc
            limit 51
            """,
        ),
        (
            "annotations viewport, 10s window (list_annotations, small window)",
            """
            select * from annotations
            where recording_id = %(heavy_annotation_recording_id)s
              and deleted_at is null
              and onset_seconds >= 100 and onset_seconds < 110
            order by onset_seconds, id
            limit 201
            """,
        ),
        (
            "annotations, no window, first page (heavy recording)",
            """
            select * from annotations
            where recording_id = %(heavy_annotation_recording_id)s and deleted_at is null
            order by onset_seconds, id
            limit 201
            """,
        ),
        (
            "bad-channel state for active version",
            """
            select * from bad_channels
            where recording_id = %(any_recording_id)s
            """,
        ),
        (
            "QC metrics + flags for a version",
            """
            select m.*, f.id as flag_id, f.severity, f.safe_message, f.state
            from qc_metrics m
            left join qc_flags f on f.metric_id = m.id
            where m.version_id = %(version_id)s
            """,
        ),
        (
            "pending job discovery (find_undispatched_jobs)",
            """
            select * from jobs
            where status = 'QUEUED' and dispatched_at is null
              and created_at < now() - interval '5 seconds'
            order by created_at
            limit 100
            """,
        ),
        (
            "lease reclaim candidates (reclaim_expired_leases select phase)",
            """
            select id from jobs
            where status = 'RUNNING' and lease_expires_at is not null
              and lease_expires_at < now()
            limit 200
            """,
        ),
        (
            "job status polling (get_job, by PK)",
            "select * from jobs where id = %(any_recording_id)s",
        ),
        (
            "idempotency lookup (create_processing_run's _existing_run)",
            """
            select * from jobs
            where workspace_id = %(workspace_id)s and idempotency_key = 'does-not-exist'
            """,
        ),
        (
            "audit events by workspace and time",
            """
            select * from audit_events
            where workspace_id = %(workspace_id)s
            order by created_at desc
            limit 50
            """,
        ),
        (
            "version lineage (recording_versions by recording)",
            "select * from recording_versions where recording_id = %(any_recording_id)s "
            "order by created_at",
        ),
        (
            "processing runs by recording",
            "select * from processing_runs where recording_id = %(any_recording_id)s "
            "order by created_at desc",
        ),
        (
            "exports by recording",
            "select * from exports where recording_id = %(any_recording_id)s "
            "order by created_at desc",
        ),
    ]

    print("\n\n===================== EXPLAIN (ANALYZE, BUFFERS) =====================")
    for label, sql in queries:
        try:
            explain(conn, label, sql, samples)
        except Exception as exc:
            print(f"\n--- {label} ---\nSKIPPED: {exc}")

    print("\n\n===================== TIMING (repeated, p50/p95/p99) =====================")
    for label, sql in queries:
        try:
            time_query(conn, label, sql, samples)
        except Exception as exc:
            print(f"  {label}: SKIPPED ({exc})")


if __name__ == "__main__":
    main()
