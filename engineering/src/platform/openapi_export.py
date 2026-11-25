"""Export the platform OpenAPI document for TypeScript contract generation.

The app is built against a temporary root so exporting never opens or writes the
working SQLite database.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .app import create_app

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json"


def build_openapi() -> dict:
    with tempfile.TemporaryDirectory(prefix="waterpipnet-openapi-") as tmp:
        app = create_app(root=Path(tmp), start_worker=False, bootstrap=False)
        return app.openapi()


def export(path: Path = DEFAULT) -> Path:
    schema = build_openapi()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="导出 WaterPipNet 平台 OpenAPI JSON")
    parser.add_argument("--output", type=Path, default=DEFAULT)
    args = parser.parse_args()
    print(export(args.output))
