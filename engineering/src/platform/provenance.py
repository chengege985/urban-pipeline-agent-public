"""Server-side synthetic fixture provenance verification."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .schemas import RecomputeRequest, SnapshotPayload
from .store import canonical, fingerprint

EXPECTED_FIXTURE_VERSION = "m1a-unified-1.0.0"
FIXTURE_MANIFEST = "m1a_unified_manifest.json"
FIXTURE_SNAPSHOT = "m1a_unified.json"
FIXTURE_INPUTS = "m1a_unified_inputs.json"
S01_SNAPSHOT = "s01.json"
S01_FILE_SHA256 = "12022d6fd60f15a1b60c35943b2db87eff5e95060b5599588a85125529f9942b"
S01_SNAPSHOT_DIGEST = "4e24a0eb474a42c359741d753d294bb5de6f10c02c8a9b8f796c6293a286fce5"


@dataclass(frozen=True)
class SyntheticProvenance:
    fixture_id: str
    source_refs: tuple[str, ...]
    snapshot_digest: str
    inputs_digest: str
    snapshot: SnapshotPayload
    request_template: RecomputeRequest


def _unavailable() -> HTTPException:
    return HTTPException(503, "服务端合成数据登记不可用")


def _rejected() -> HTTPException:
    return HTTPException(403, "请求数据未通过服务端合成来源验证")


def _fixture_root() -> Path:
    raw = os.getenv("WATER_SYNTHETIC_FIXTURE_ROOT", "").strip()
    candidate = Path(raw).expanduser() if raw else Path(__file__).resolve().parents[2] / "data" / "synthetic"
    code_root = Path(__file__).resolve().parents[2]
    default_root = code_root / "data" / "synthetic"
    try:
        if candidate.is_symlink():
            raise ValueError
        root = candidate.resolve(strict=True)
        allowed = [default_root]
        configured = os.getenv("WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS", "")
        for item in configured.split(os.pathsep):
            if not item.strip():
                continue
            allowed_root = Path(item).expanduser()
            if allowed_root.is_symlink():
                raise ValueError
            allowed.append(allowed_root.resolve(strict=True))
        if not root.is_dir() or not any(root == item or root.is_relative_to(item) for item in allowed):
            raise ValueError
        return root
    except (OSError, RuntimeError, ValueError):
        raise _unavailable() from None


def _safe_file(root: Path, name: str) -> Path:
    path = root / name
    try:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError
        return path
    except (OSError, RuntimeError, ValueError):
        raise _unavailable() from None


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise _unavailable() from None
    if not isinstance(value, dict):
        raise _unavailable()
    return value, raw


def load_trusted_synthetic_fixture() -> SyntheticProvenance:
    root = _fixture_root()
    manifest, _ = _read_json(_safe_file(root, FIXTURE_MANIFEST))
    snapshot_raw, snapshot_bytes = _read_json(_safe_file(root, FIXTURE_SNAPSHOT))
    inputs, inputs_bytes = _read_json(_safe_file(root, FIXTURE_INPUTS))
    if manifest.get("fixture_version") != EXPECTED_FIXTURE_VERSION:
        raise _unavailable()
    try:
        snapshot_file_sha = hashlib.sha256(snapshot_bytes).hexdigest()
        inputs_sha = hashlib.sha256(inputs_bytes).hexdigest()
        if snapshot_file_sha != manifest["snapshot_file_sha256"] or inputs_sha != manifest["inputs_sha256"]:
            raise ValueError
        snapshot_digest = fingerprint(snapshot_raw)
        if snapshot_digest != manifest["snapshot_digest"]:
            raise ValueError
        snapshot = SnapshotPayload.model_validate(snapshot_raw)
        template_data = {
            "name": "受控合成 Fixture 重算",
            "as_of": inputs["as_of"],
            "window_start": inputs["window_start"],
            "window_end": inputs["window_end"],
            "static_rows": inputs["static_rows"],
            "dynamic_rows": inputs["dynamic_rows"],
            "data_mode": "synthetic",
            "source_refs": list(snapshot.source_refs),
        }
        template = RecomputeRequest.model_validate(template_data)
    except (KeyError, TypeError, ValueError):
        raise _unavailable() from None
    if snapshot.data_mode != "synthetic" or snapshot.source_status != "constructed":
        raise _unavailable()
    return SyntheticProvenance(
        fixture_id=EXPECTED_FIXTURE_VERSION,
        source_refs=tuple(snapshot.source_refs),
        snapshot_digest=manifest["snapshot_digest"],
        inputs_digest=manifest["inputs_sha256"],
        snapshot=snapshot,
        request_template=template,
    )


def _request_core(request: RecomputeRequest) -> dict[str, Any]:
    value = request.model_dump(mode="json")
    return {key: value[key] for key in (
        "as_of", "window_start", "window_end", "static_rows", "dynamic_rows"
    )}


def verify_assessment_request(request: RecomputeRequest) -> SyntheticProvenance:
    if isinstance(request, dict):
        try:
            request = RecomputeRequest.model_validate(request)
        except ValueError:
            raise _rejected() from None
    trusted = load_trusted_synthetic_fixture()
    if request.data_mode != "synthetic":
        raise _rejected()
    if canonical(_request_core(request)) != canonical(_request_core(trusted.request_template)):
        raise _rejected()
    return trusted


def with_trusted_assessment_provenance(
    request: RecomputeRequest, trusted: SyntheticProvenance
) -> RecomputeRequest:
    return request.model_copy(update={"source_refs": list(trusted.source_refs)})


def verify_synthetic_snapshot(payload: SnapshotPayload) -> SnapshotPayload:
    candidate = payload.model_dump(mode="json")
    trusted = load_trusted_synthetic_fixture()
    candidate["source_refs"] = list(trusted.snapshot.source_refs)
    if canonical(candidate) == canonical(trusted.snapshot.model_dump(mode="json")):
        return trusted.snapshot
    root = _fixture_root()
    s01_raw, s01_bytes = _read_json(_safe_file(root, S01_SNAPSHOT))
    try:
        if hashlib.sha256(s01_bytes).hexdigest() != S01_FILE_SHA256:
            raise ValueError
        if fingerprint(s01_raw) != S01_SNAPSHOT_DIGEST:
            raise ValueError
    except (TypeError, ValueError):
        raise _unavailable() from None
    server_snapshot = SnapshotPayload.model_validate(s01_raw)
    candidate["source_refs"] = list(server_snapshot.source_refs)
    if canonical(candidate) == canonical(server_snapshot.model_dump(mode="json")):
        return server_snapshot
    raise _rejected()
