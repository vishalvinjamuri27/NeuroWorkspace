"""Remove only known, reproducible local build and test artifacts."""

from __future__ import annotations

import shutil
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    relative_targets = (
        ".coverage",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "htmlcov",
        "playwright-report",
        "test-results",
        "apps/web/.next",
        "apps/web/tsconfig.tsbuildinfo",
    )
    for relative in relative_targets:
        target = (root / relative).resolve()
        if root not in target.parents:
            raise RuntimeError(f"refusing to clean outside repository: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        print(f"cleaned {relative}")


if __name__ == "__main__":
    main()
