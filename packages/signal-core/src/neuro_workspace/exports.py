"""Safe interoperable exports that do not trap users in the application."""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def safe_spreadsheet_text(value: object) -> str:
    text = str(value)
    return f"'{text}" if text.startswith(FORMULA_PREFIXES) else text


def export_annotations_csv(rows: Iterable[Mapping[str, Any]], destination: Path) -> None:
    fields = [
        "id",
        "onset_seconds",
        "duration_seconds",
        "label",
        "description",
        "creator_id",
        "revision",
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: safe_spreadsheet_text(row.get(field, "")) for field in fields})


def export_provenance_json(
    *,
    recording: Mapping[str, Any],
    versions: Sequence[Mapping[str, Any]],
    processing_runs: Sequence[Mapping[str, Any]],
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "schema": "org.neuroworkspace.provenance.v1",
                "recording": dict(recording),
                "versions": [dict(version) for version in versions],
                "processing_runs": [dict(run) for run in processing_runs],
            },
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
