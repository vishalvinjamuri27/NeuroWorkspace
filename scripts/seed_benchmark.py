"""Deterministic, realistic-scale data generator for the performance benchmarks
recorded in docs/PERFORMANCE.md.

This is a benchmarking tool, not part of the application. It writes directly
with SQLAlchemy Core bulk inserts (not the ORM, and not through the API) so
that seeding a few hundred thousand rows takes minutes rather than hours --
the queries and indexes under test are the same either way, since row shape
and distribution are what matters, not how the rows were written.

Usage:

    DATABASE_URL=postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace \\
        uv run python scripts/seed_benchmark.py --scale medium --reset

Scale profiles (see SCALES below) are deliberately tuned so each one finishes
in a practical amount of time on a single development machine while still
producing the row-count order of magnitude its name promises. Exact row
counts are printed at the end of the run and are what docs/PERFORMANCE.md
cites -- never assume the numbers below without checking that output, since
skew/rounding means actual counts differ slightly from the targets.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, "packages/signal-core/src")

from neuro_workspace.db.models import Base

SCALES: dict[str, dict[str, int]] = {
    # Development-loop scale: seeds in well under a minute.
    "small": dict(
        users=25,
        workspaces=8,
        recordings=60,
        annotations_target=2_000,
        product_events_target=3_000,
    ),
    # "Thousands of recordings, tens/hundreds of thousands of review
    # artifacts" per the task brief.
    "medium": dict(
        users=800,
        workspaces=120,
        recordings=4_000,
        annotations_target=150_000,
        product_events_target=60_000,
    ),
    # Enough rows on the highest-volume tables (annotations, qc_metrics,
    # audit_events) to exercise index/query-planning behavior at real scale.
    # recordings itself is not pushed to "millions" -- a workspace with a
    # million recordings is not a realistic distribution for this product,
    # whereas a well-reviewed recording accumulating thousands of
    # annotations, or years of audit history, is.
    "large": dict(
        users=5_000,
        workspaces=600,
        recordings=25_000,
        annotations_target=800_000,
        product_events_target=300_000,
    ),
}

WORDS = [
    "delta",
    "gamma",
    "theta",
    "alpha",
    "beta",
    "sigma",
    "omega",
    "frontal",
    "parietal",
    "occipital",
    "temporal",
    "midline",
    "lateral",
    "resting",
    "eyes-open",
    "eyes-closed",
    "baseline",
    "motor",
    "sleep",
    "stage",
    "artifact",
    "spike",
    "burst",
    "drift",
    "saccade",
    "blink",
    "review",
    "checkpoint",
    "cohort",
    "pilot",
    "longitudinal",
    "followup",
    "screening",
    "calibration",
    "replicate",
]


def word(rng: random.Random, n: int = 2) -> str:
    return " ".join(rng.choice(WORDS) for _ in range(n))


@dataclass
class Counters:
    """Every row-generating function pulls its ids from here so foreign
    keys are always valid -- ids are assigned up front, never invented at
    reference time."""

    n: int = 0

    def id(self) -> str:
        self.n += 1
        return str(uuid.uuid4())


def chunked(rows: list[dict], size: int = 2_000):
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def bulk_insert(conn, table_name: str, rows: list[dict], label: str | None = None) -> float:
    if not rows:
        return 0.0
    table = Base.metadata.tables[table_name]
    start = time.perf_counter()
    for batch in chunked(rows):
        conn.execute(table.insert(), batch)
    elapsed = time.perf_counter() - start
    rate = len(rows) / elapsed if elapsed > 0 else float("inf")
    print(f"  {label or table_name}: {len(rows):>9,} rows in {elapsed:7.2f}s ({rate:9,.0f} rows/s)")
    return elapsed


def zipf_bucket(rng: random.Random, n: int, heavy_fraction: float = 0.08) -> list[int]:
    """Split `n` items into buckets with a realistic long tail: most buckets
    get a small share, a `heavy_fraction` of buckets get most of the mass.
    Returns a list of per-bucket counts. Used for "most recordings have few
    annotations, a few have thousands" and similar distributions.
    """

    if n <= 0:
        return []
    heavy = max(1, int(n * heavy_fraction))
    light = n - heavy
    return [rng.randint(1, 6) for _ in range(light)] + [
        rng.randint(80, 4_000) for _ in range(heavy)
    ]


def now_minus(rng: random.Random, max_days: int) -> datetime:
    return datetime.now(UTC) - timedelta(
        days=rng.uniform(0, max_days), seconds=rng.uniform(0, 86_400)
    )


def seed(database_url: str, scale: str, seed_value: int, reset: bool) -> None:
    cfg = SCALES[scale]
    rng = random.Random(seed_value)  # noqa: S311 -- deterministic test-data generation, not crypto
    engine = create_engine(database_url)

    if reset:
        print("Resetting benchmark tables (TRUNCATE ... CASCADE) ...")
        with engine.begin() as conn:
            tables = ", ".join(
                t.name for t in reversed(Base.metadata.sorted_tables) if t.name != "alembic_version"
            )
            conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))

    total_start = time.perf_counter()
    ids: dict[str, list[str]] = {}

    with engine.begin() as conn:
        # --- users --------------------------------------------------
        users = []
        for i in range(cfg["users"]):
            uid = str(uuid.uuid4())
            users.append(
                dict(
                    id=uid,
                    external_subject=f"bench-subject-{seed_value}-{i}",
                    email=f"bench-user-{seed_value}-{i}@example.test",
                    display_name=f"Bench User {i}",
                    is_active=True,
                    created_at=now_minus(rng, 400),
                    updated_at=now_minus(rng, 30),
                )
            )
        ids["users"] = [u["id"] for u in users]
        bulk_insert(conn, "users", users)

        # --- workspaces + memberships --------------------------------
        # Most workspaces are modest (1-4 members); a handful ("labs") are
        # large (dozens of members) -- skewed the same way real
        # organizations skew.
        workspaces = []
        memberships = []
        member_sizes = zipf_bucket(rng, cfg["workspaces"], heavy_fraction=0.1)
        for i, member_count in enumerate(member_sizes):
            wid = str(uuid.uuid4())
            workspaces.append(
                dict(
                    id=wid,
                    name=f"{word(rng)} lab {i}",
                    slug=f"bench-ws-{seed_value}-{i}",
                    created_at=now_minus(rng, 400),
                    updated_at=now_minus(rng, 30),
                )
            )
            member_count = min(member_count, len(users))
            chosen = rng.sample(users, member_count)
            for j, user in enumerate(chosen):
                memberships.append(
                    dict(
                        id=str(uuid.uuid4()),
                        workspace_id=wid,
                        user_id=user["id"],
                        role="owner"
                        if j == 0
                        else rng.choice(["admin", "member", "member", "viewer"]),
                        is_active=True,
                        created_at=now_minus(rng, 380),
                        updated_at=now_minus(rng, 20),
                    )
                )
        ids["workspaces"] = [w["id"] for w in workspaces]
        bulk_insert(conn, "workspaces", workspaces)
        bulk_insert(conn, "memberships", memberships)

        # --- projects ---------------------------------------------
        projects = []
        project_sizes = zipf_bucket(rng, len(workspaces), heavy_fraction=0.15)
        for wid, count in zip(ids["workspaces"], project_sizes, strict=True):
            count = max(1, min(count, 40))
            for _ in range(count):
                projects.append(
                    dict(
                        id=str(uuid.uuid4()),
                        workspace_id=wid,
                        name=f"{word(rng)} study",
                        description=word(rng, 6),
                        created_at=now_minus(rng, 350),
                        updated_at=now_minus(rng, 15),
                    )
                )
        ids["projects"] = [p["id"] for p in projects]
        bulk_insert(conn, "projects", projects)

        # --- recordings + uploads/source_assets --------------------
        target_recordings = cfg["recordings"]
        recording_sizes = zipf_bucket(rng, len(projects), heavy_fraction=0.1)
        total = sum(recording_sizes)
        scale_factor = target_recordings / total if total else 1
        recording_sizes = [max(0, round(c * scale_factor)) for c in recording_sizes]

        formats = ["edf", "bdf", "brainvision", "eeglab", "fif", "csv"]
        recordings = []
        uploads = []
        source_assets = []
        for pid, count in zip(ids["projects"], recording_sizes, strict=True):
            workspace_id = next(p["workspace_id"] for p in projects if p["id"] == pid)
            creator = rng.choice(users)["id"]
            for _ in range(count):
                rid = str(uuid.uuid4())
                uid_ = str(uuid.uuid4())
                created = now_minus(rng, 300)
                fmt = rng.choice(formats)
                duration = rng.choice([30, 60, 300, 600, 1800, 3600, 7200])
                sample_rate = rng.choice([100, 128, 200, 256, 500, 1000])
                channel_count = rng.choice([8, 16, 21, 32, 64, 128])
                recordings.append(
                    dict(
                        id=rid,
                        workspace_id=workspace_id,
                        project_id=pid,
                        creator_id=creator,
                        name=f"{word(rng, 2)} recording",
                        format_id=fmt,
                        state="READY",
                        active_version_id=None,  # filled in after versions exist
                        source_provenance={"kind": "synthetic-benchmark"},
                        safe_metadata={
                            "duration_seconds": duration,
                            "sampling_frequency_hz": sample_rate,
                            "channel_count": channel_count,
                            "sample_count": duration * sample_rate,
                        },
                        is_demo=False,
                        error_code=None,
                        error_message=None,
                        deleted_at=None,
                        created_at=created,
                        updated_at=created,
                    )
                )
                uploads.append(
                    dict(
                        id=uid_,
                        workspace_id=workspace_id,
                        project_id=pid,
                        creator_id=creator,
                        status="COMPLETE",
                        format_hint=fmt,
                        csv_config=None,
                        total_bytes=min(duration * sample_rate * channel_count * 4, 2_000_000_000),
                        completed_at=created,
                        created_at=created,
                        updated_at=created,
                    )
                )
                source_assets.append(
                    dict(
                        id=str(uuid.uuid4()),
                        upload_id=uid_,
                        recording_id=rid,
                        object_key=f"bench/{seed_value}/{rid}.{fmt}",
                        safe_name=f"recording.{fmt}",
                        role="primary",
                        size_bytes=min(duration * sample_rate * channel_count * 4, 2_000_000_000),
                        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
                        media_type=None,
                        created_at=created,
                        updated_at=created,
                    )
                )
        ids["recordings"] = [r["id"] for r in recordings]
        bulk_insert(conn, "uploads", uploads)
        bulk_insert(conn, "recordings", recordings)
        bulk_insert(conn, "source_assets", source_assets)

        # --- recording_versions + channels ---------------------------
        # ~85% of recordings have exactly one version (the ingested raw
        # version); ~15% also have a derived version from a processing run
        # (see processing_runs below), matching services.jobs'
        # execute_processing_job behavior of creating exactly one derived
        # version per successful run.
        versions = []
        channels = []
        version_by_recording: dict[str, list[str]] = {}
        for rec in recordings:
            base_version_id = str(uuid.uuid4())
            channel_count = rec["safe_metadata"]["channel_count"]
            version_by_recording[rec["id"]] = [base_version_id]
            versions.append(
                dict(
                    id=base_version_id,
                    recording_id=rec["id"],
                    parent_version_id=None,
                    kind="normalized_raw",
                    store_key=base_version_id,
                    manifest_sha256=uuid.uuid4().hex,
                    sampling_frequency_hz=rec["safe_metadata"]["sampling_frequency_hz"],
                    sample_count=rec["safe_metadata"]["sample_count"],
                    duration_seconds=rec["safe_metadata"]["duration_seconds"],
                    channel_count=channel_count,
                    software={"adapter": rec["format_id"]},
                    ready_at=rec["created_at"],
                    created_at=rec["created_at"],
                    updated_at=rec["created_at"],
                )
            )
            for idx in range(channel_count):
                channels.append(
                    dict(
                        id=str(uuid.uuid4()),
                        version_id=base_version_id,
                        channel_index=idx,
                        name=f"CH{idx}",
                        source_name=f"CH{idx}",
                        channel_type="eeg",
                        unit="V",
                        source_unit="uV",
                        conversion_to_si=1e-6,
                        source_sampling_frequency_hz=rec["safe_metadata"]["sampling_frequency_hz"],
                    )
                )
            rec["active_version_id"] = base_version_id
            if rng.random() < 0.15:
                derived_id = str(uuid.uuid4())
                derived_created = rec["created_at"] + timedelta(minutes=rng.uniform(1, 120))
                versions.append(
                    dict(
                        id=derived_id,
                        recording_id=rec["id"],
                        parent_version_id=base_version_id,
                        kind="derived",
                        store_key=derived_id,
                        manifest_sha256=uuid.uuid4().hex,
                        sampling_frequency_hz=rec["safe_metadata"]["sampling_frequency_hz"],
                        sample_count=rec["safe_metadata"]["sample_count"],
                        duration_seconds=rec["safe_metadata"]["duration_seconds"],
                        channel_count=channel_count,
                        software={"operation": "average_reference"},
                        ready_at=derived_created,
                        created_at=derived_created,
                        updated_at=derived_created,
                    )
                )
                for idx in range(channel_count):
                    channels.append(
                        dict(
                            id=str(uuid.uuid4()),
                            version_id=derived_id,
                            channel_index=idx,
                            name=f"CH{idx}",
                            source_name=f"CH{idx}",
                            channel_type="eeg",
                            unit="V",
                            source_unit="uV",
                            conversion_to_si=1e-6,
                            source_sampling_frequency_hz=rec["safe_metadata"][
                                "sampling_frequency_hz"
                            ],
                        )
                    )
                version_by_recording[rec["id"]].append(derived_id)
                rec["active_version_id"] = derived_id
        bulk_insert(conn, "recording_versions", versions)
        bulk_insert(conn, "channels", channels)
        # active_version_id was only known after versions were generated.
        conn.execute(text("SET LOCAL synchronous_commit = off"))
        recon = [
            {"rec_id": r["id"], "active_version_id": r["active_version_id"]} for r in recordings
        ]
        table = Base.metadata.tables["recordings"]
        from sqlalchemy import bindparam

        stmt = (
            table.update()
            .where(table.c.id == bindparam("rec_id"))
            .values(active_version_id=bindparam("active_version_id"))
        )
        start = time.perf_counter()
        for batch in chunked(recon):
            conn.execute(stmt, batch)
        print(
            f"  recordings.active_version_id backfill: {len(recon):,} rows in "
            f"{time.perf_counter() - start:.2f}s"
        )

        # --- annotations (clustered by recording/time) ----------------
        # Most recordings get a light, uniformly-scattered handful of
        # annotations; the heavy tail gets "review sessions" -- bursts of
        # annotations clustered tightly in time, the way a human reviewer
        # actually works through a recording rather than annotating
        # uniformly at random.
        annotations = []
        per_recording = zipf_bucket(rng, len(recordings), heavy_fraction=0.05)
        total = sum(per_recording)
        scale_factor = cfg["annotations_target"] / total if total else 1
        per_recording = [max(0, round(c * scale_factor)) for c in per_recording]
        labels = ["spike", "artifact", "eyes-open", "eyes-closed", "movement", "review-flag"]
        for rec, count in zip(recordings, per_recording, strict=True):
            duration = rec["safe_metadata"]["duration_seconds"]
            if count == 0:
                continue
            creator = rng.choice(users)["id"]
            remaining = count
            while remaining > 0:
                burst = min(remaining, rng.randint(3, 25))
                cluster_center = rng.uniform(0, max(0.01, duration - 1))
                for _ in range(burst):
                    onset = min(
                        max(0.0, rng.gauss(cluster_center, duration * 0.02)), max(0, duration - 0.5)
                    )
                    created = rec["created_at"] + timedelta(minutes=rng.uniform(1, 500))
                    annotations.append(
                        dict(
                            id=str(uuid.uuid4()),
                            recording_id=rec["id"],
                            version_id=rec["active_version_id"],
                            creator_id=creator,
                            updater_id=creator,
                            onset_seconds=round(onset, 3),
                            duration_seconds=round(rng.uniform(0, 2), 3),
                            label=rng.choice(labels),
                            description="",
                            provenance_kind="user",
                            revision=1,
                            deleted_at=None,
                            created_at=created,
                            updated_at=created,
                        )
                    )
                remaining -= burst
        bulk_insert(conn, "annotations", annotations)

        # --- bad_channels: ~10% of (version, channel) pairs reviewed --
        bad_channels = []
        chan_by_version: dict[str, list[dict]] = {}
        for c in channels:
            chan_by_version.setdefault(c["version_id"], []).append(c)
        for rec in recordings:
            version_id = rec["active_version_id"]
            version_channels = chan_by_version.get(version_id, [])
            if not version_channels:
                continue
            reviewed = rng.sample(
                version_channels, k=max(0, round(len(version_channels) * rng.uniform(0, 0.25)))
            )
            actor = rng.choice(users)["id"]
            for ch in reviewed:
                bad_channels.append(
                    dict(
                        id=str(uuid.uuid4()),
                        recording_id=rec["id"],
                        version_id=version_id,
                        channel_id=ch["id"],
                        actor_id=actor,
                        state=rng.choice(["confirmed_bad", "confirmed_bad", "confirmed_good"]),
                        reason=word(rng, 3),
                        revision=1,
                        created_at=now_minus(rng, 200),
                        updated_at=now_minus(rng, 20),
                    )
                )
        bulk_insert(conn, "bad_channels", bad_channels)

        # --- qc_metrics + qc_flags: a handful of rules per channel -----
        rules = ["flatline", "high_amplitude", "line_noise", "impedance_drift"]
        qc_metrics = []
        qc_flags = []
        for version in versions:
            version_channels = chan_by_version.get(version["id"], [])
            for ch in version_channels:
                for rule in rules:
                    metric_id = str(uuid.uuid4())
                    value = rng.uniform(0, 100)
                    qc_metrics.append(
                        dict(
                            id=metric_id,
                            version_id=version["id"],
                            channel_id=ch["id"],
                            rule_id=rule,
                            rule_version="1",
                            value=round(value, 3),
                            unit="1",
                            parameters={},
                            coverage={"fraction": 1.0},
                            created_at=version["created_at"],
                            updated_at=version["created_at"],
                        )
                    )
                    if value > 92:
                        qc_flags.append(
                            dict(
                                id=str(uuid.uuid4()),
                                metric_id=metric_id,
                                severity="review",
                                threshold=92.0,
                                comparison="gt",
                                safe_message=f"{rule} exceeded the review threshold",
                                state="algorithmic",
                                reviewer_id=None,
                                created_at=version["created_at"],
                                updated_at=version["created_at"],
                            )
                        )
        bulk_insert(conn, "qc_metrics", qc_metrics)
        bulk_insert(conn, "qc_flags", qc_flags)

        # --- jobs: skewed toward SUCCEEDED, mostly INGESTION -----------
        jobs = []
        processing_runs = []
        job_state_weights = [
            ("SUCCEEDED", 80),
            ("FAILED", 10),
            ("QUEUED", 5),
            ("RUNNING", 3),
            ("CANCELLED", 2),
        ]
        states, weights = zip(*job_state_weights, strict=True)
        for rec in recordings:
            job_id = str(uuid.uuid4())
            created = rec["created_at"]
            status = rng.choices(states, weights=weights, k=1)[0]
            jobs.append(
                dict(
                    id=job_id,
                    workspace_id=rec["workspace_id"],
                    kind="INGESTION",
                    subject_type="recording",
                    subject_id=rec["id"],
                    idempotency_key=f"ingest:{rec['id']}",
                    status=status,
                    attempt=1 if status != "FAILED" else rng.randint(1, 3),
                    max_attempts=3,
                    progress=1.0 if status == "SUCCEEDED" else rng.uniform(0, 0.9),
                    stage="Ready" if status == "SUCCEEDED" else "Processing",
                    error_code="ingest_failed" if status == "FAILED" else None,
                    error_message="Synthetic benchmark failure" if status == "FAILED" else None,
                    diagnostic_id=str(uuid.uuid4()) if status == "FAILED" else None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    dispatched_at=created,
                    created_at=created,
                    updated_at=created,
                )
            )
            versions_for_rec = version_by_recording[rec["id"]]
            if len(versions_for_rec) > 1:
                proc_job_id = str(uuid.uuid4())
                proc_created = rec["created_at"] + timedelta(minutes=5)
                jobs.append(
                    dict(
                        id=proc_job_id,
                        workspace_id=rec["workspace_id"],
                        kind="PROCESSING",
                        subject_type="recording",
                        subject_id=rec["id"],
                        idempotency_key=f"process:{rec['id']}:{proc_job_id}",
                        status="SUCCEEDED",
                        attempt=1,
                        max_attempts=3,
                        progress=1.0,
                        stage="Ready",
                        error_code=None,
                        error_message=None,
                        diagnostic_id=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                        dispatched_at=proc_created,
                        created_at=proc_created,
                        updated_at=proc_created,
                    )
                )
                processing_runs.append(
                    dict(
                        id=str(uuid.uuid4()),
                        recording_id=rec["id"],
                        input_version_id=versions_for_rec[0],
                        output_version_id=versions_for_rec[1],
                        creator_id=rng.choice(users)["id"],
                        job_id=proc_job_id,
                        operation_id="average_reference",
                        operation_version="1",
                        parameters={
                            "channel_indices": list(
                                range(min(4, rec["safe_metadata"]["channel_count"]))
                            )
                        },
                        status="SUCCEEDED",
                        created_at=proc_created,
                        updated_at=proc_created,
                    )
                )
        bulk_insert(conn, "jobs", jobs)
        bulk_insert(conn, "processing_runs", processing_runs)

        # --- exports: 0-3 per recording, mostly READY -------------------
        exports = []
        for rec in recordings:
            if rng.random() > 0.2:
                continue
            for _ in range(rng.randint(1, 3)):
                created = rec["created_at"] + timedelta(minutes=rng.uniform(5, 1000))
                exports.append(
                    dict(
                        id=str(uuid.uuid4()),
                        recording_id=rec["id"],
                        version_id=rec["active_version_id"],
                        creator_id=rng.choice(users)["id"],
                        job_id=None,
                        format_id=rng.choice(["annotations_csv", "provenance_json", "qc_json"]),
                        options={},
                        status="READY",
                        object_key=f"bench/exports/{rec['id']}/{uuid.uuid4().hex}.json",
                        sha256=uuid.uuid4().hex,
                        size_bytes=rng.randint(200, 20_000),
                        loss_notes=[],
                        created_at=created,
                        updated_at=created,
                    )
                )
        bulk_insert(conn, "exports", exports)

        # --- audit_events: high-volume, several per recording lifecycle -
        audit_events = []
        actions = [
            "viewer.opened",
            "annotation.created",
            "annotation.updated",
            "bad_channel.marked",
            "export.created",
            "recording.uploaded",
        ]
        for rec in recordings:
            burst = rng.randint(2, 30)
            for _ in range(burst):
                audit_events.append(
                    dict(
                        id=str(uuid.uuid4()),
                        workspace_id=rec["workspace_id"],
                        actor_id=rng.choice(users)["id"],
                        action=rng.choice(actions),
                        target_type="recording",
                        target_id=rec["id"],
                        safe_fields={},
                        correlation_id=str(uuid.uuid4()),
                        created_at=rec["created_at"] + timedelta(minutes=rng.uniform(0, 2000)),
                    )
                )
        bulk_insert(conn, "audit_events", audit_events)

        # --- product_events: high-volume, name+time distributed ---------
        product_events = []
        names = [
            "recording_opened",
            "viewer_zoomed",
            "viewer_panned",
            "channel_visibility_changed",
            "qc_opened",
            "sample_recording_opened",
        ]
        target = cfg["product_events_target"]
        for _ in range(target):
            rec = rng.choice(recordings)
            product_events.append(
                dict(
                    id=str(uuid.uuid4()),
                    name=rng.choice(names),
                    actor_id=rng.choice(users)["id"],
                    workspace_id=rec["workspace_id"],
                    recording_id=rec["id"],
                    session_id=uuid.uuid4().hex,
                    safe_properties={},
                    created_at=rec["created_at"] + timedelta(minutes=rng.uniform(0, 2000)),
                )
            )
        bulk_insert(conn, "product_events", product_events)

    total_elapsed = time.perf_counter() - total_start
    print(f"\nDone in {total_elapsed:.1f}s.")

    with engine.connect() as conn:
        print("\nFinal row counts:")
        for t in [
            "users",
            "workspaces",
            "memberships",
            "projects",
            "recordings",
            "recording_versions",
            "channels",
            "annotations",
            "bad_channels",
            "qc_metrics",
            "qc_flags",
            "jobs",
            "processing_runs",
            "exports",
            "audit_events",
            "product_events",
        ]:
            # `t` is drawn from the fixed table-name list literally above,
            # never from external input.
            n = conn.execute(text(f"select count(*) from {t}")).scalar()  # noqa: S608
            print(f"  {t:<22} {n:>10,}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scale", choices=sorted(SCALES), default="small")
    parser.add_argument(
        "--database-url",
        default="postgresql+psycopg://neuroworkspace:neuroworkspace@localhost:5433/neuroworkspace",
    )
    parser.add_argument("--seed", type=int, default=20260904)
    parser.add_argument("--reset", action="store_true", help="TRUNCATE all tables first")
    args = parser.parse_args()
    seed(args.database_url, args.scale, args.seed, args.reset)


if __name__ == "__main__":
    main()
