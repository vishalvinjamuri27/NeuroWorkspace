"""Ethically fetch checksum-pinned public acceptance data into an ignored cache."""

from __future__ import annotations

import argparse
import hashlib
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import certifi
import yaml


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(root: Path) -> dict[str, Any]:
    return dict(yaml.safe_load((root / "testdata" / "manifest.yaml").read_text(encoding="utf-8")))


def destination_for(cache: Path, file_spec: dict[str, Any]) -> Path:
    destination = (cache / str(file_spec["path"])).resolve()
    if cache.resolve() not in destination.parents:
        raise ValueError("manifest path escapes the test-data cache")
    return destination


def fetch_file(file_spec: dict[str, Any], destination: Path) -> None:
    expected_hash = str(file_spec.get("sha256") or "")
    expected_size = int(file_spec.get("size_bytes") or 0)
    if len(expected_hash) != 64 or expected_size <= 0:
        raise ValueError(f"{file_spec['path']} is not checksum and size pinned")
    if destination.is_file() and destination.stat().st_size == expected_size:
        if sha256(destination) == expected_hash:
            print(f"verified {destination.name} ({expected_size} bytes)")
            return
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    request = urllib.request.Request(
        str(file_spec["url"]), headers={"Range": f"bytes={offset}-"} if offset else {}
    )
    try:
        tls_context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(request, timeout=60, context=tls_context) as response:
            if offset and response.status != 206:
                offset = 0
                partial.unlink(missing_ok=True)
            mode = "ab" if offset else "wb"
            with partial.open(mode) as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    print(
                        f"fetching {destination.name}: {output.tell()}/{expected_size} bytes",
                        end="\r",
                    )
    except urllib.error.URLError as exc:
        raise RuntimeError(f"download failed for {destination.name}: {exc.reason}") from exc
    print()
    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"size mismatch for {destination.name}: {partial.stat().st_size} != {expected_size}"
        )
    actual_hash = sha256(partial)
    if actual_hash != expected_hash:
        raise RuntimeError(f"checksum mismatch for {destination.name}")
    partial.replace(destination)
    print(f"verified {destination.name} ({expected_size} bytes)")


def list_samples(manifest: dict[str, Any], cache: Path) -> None:
    for sample_id, raw_sample in manifest["samples"].items():
        sample = dict(raw_sample)
        files = list(sample.get("files") or [])
        cached = sum(destination_for(cache, dict(item)).is_file() for item in files)
        print(
            f"{sample_id}: {sample['status']} · {sample['dataset']} · "
            f"{cached}/{len(files)} files cached"
        )


def inspect_edf(cache: Path, sample: dict[str, Any]) -> None:
    import mne

    signal_spec = next(item for item in sample["files"] if item["role"] == "signal")
    path = destination_for(cache, dict(signal_spec))
    if not path.is_file():
        raise RuntimeError("fetch the sample before inspection")
    raw = mne.io.read_raw_edf(path, preload=False, verbose="ERROR")
    try:
        safe = {
            "format": "EDF+",
            "channels": len(raw.ch_names),
            "sampling_frequency_hz": float(raw.info["sfreq"]),
            "samples": int(raw.n_times),
            "duration_seconds": int(raw.n_times) / float(raw.info["sfreq"]),
        }
        print(yaml.safe_dump(safe, sort_keys=True).strip())
    finally:
        raw.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list")
    fetch_parser = subparsers.add_parser("fetch")
    fetch_parser.add_argument("--sample", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--sample", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    manifest = load_manifest(root)
    cache = (root / str(manifest["cache_directory"])).resolve()
    if args.command == "list":
        list_samples(manifest, cache)
        return
    sample = dict(manifest["samples"].get(args.sample) or {})
    if not sample:
        parser.error(f"unknown sample: {args.sample}")
    if args.command == "fetch":
        if sample.get("status") != "pinned":
            raise RuntimeError(
                f"{args.sample} is only a candidate; verify license, size, and checksum first"
            )
        for file_spec in sample.get("files") or []:
            fetch_file(dict(file_spec), destination_for(cache, dict(file_spec)))
    elif args.command == "inspect":
        inspect_edf(cache, sample)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
