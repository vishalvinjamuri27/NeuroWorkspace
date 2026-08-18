"""Export the API contract without starting network services."""

from __future__ import annotations

import json
from pathlib import Path

from neuro_api.main import create_app
from neuro_workspace.config import Settings


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = root / "packages" / "shared-types" / "openapi.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    app = create_app(Settings(environment="test"))
    destination.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {destination.relative_to(root)}")


if __name__ == "__main__":
    main()
