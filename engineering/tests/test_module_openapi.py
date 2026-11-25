"""The existing HTTP schema stays the sole source of platform DTOs."""
import json
from pathlib import Path
from src.platform.openapi_export import build_openapi

def test_actual_platform_openapi_matches_committed_schema():
    source = Path(__file__).resolve().parents[1] / "04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json"
    assert build_openapi() == json.loads(source.read_text(encoding="utf-8"))
