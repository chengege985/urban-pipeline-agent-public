"""Read-only, offline intake of versioned gas data packages."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import stat
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .intake_rows import HEADERS, Issue, NormalizedRows, SourceRef, normalize_rows


TableName = Literal["assets", "coordinates", "monitoring"]
IntakeMode = Literal["synthetic", "real_authorized"]
Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
MAX_MANIFEST_BYTES = 32 * 1024
MAX_TABLE_BYTES = 64 * 1024 * 1024


class IntakeError(RuntimeError):
    """Stable package errors hold structural location, never record values."""

    def __init__(self, code: str, *, table: str | None = None, row: int | None = None):
        super().__init__(code)
        self.code = code
        self.table = table
        self.row = row


class ManifestSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: Identifier
    version: Identifier
    sha256: Digest


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    schema_version: int = Field(ge=1, le=1)
    dataset_id: Identifier
    version: Identifier
    data_mode: IntakeMode
    sources: dict[TableName, ManifestSource]


@dataclass(frozen=True)
class ApprovedPackage:
    """An exact package grant supplied by trusted server-side authorization code."""

    directory: Path
    dataset_id: str
    manifest_sha256: str
    approval_ref: str


@dataclass(frozen=True)
class IntakeSnapshot:
    dataset_id: str
    version: str
    data_mode: IntakeMode
    manifest_sha256: str
    sources: dict[str, SourceRef]
    received_counts: dict[str, int]
    rows: NormalizedRows = field(repr=False)
    approval_ref: str | None = field(repr=False)

    def quality_report(self) -> dict:
        """Summarize coverage and traceability without coordinates or measured values."""

        counts = {table: len(getattr(self.rows, table)) for table in HEADERS}
        assets = counts["assets"]
        with_coordinates = len({item.asset_id for item in self.rows.coordinates})
        with_monitoring = len({item.asset_id for item in self.rows.monitoring})
        issues = list(self.rows.issues)
        for table in HEADERS:
            if table not in self.sources:
                issues.append(Issue(table, "missing_source"))
            elif not counts[table]:
                issues.append(Issue(table, "no_usable_rows"))
        if assets > with_coordinates:
            issues.append(Issue("coordinates", "incomplete_asset_coverage"))
        if assets > with_monitoring:
            issues.append(Issue("monitoring", "incomplete_asset_coverage"))

        return {
            "schema_version": 1,
            "dataset_id": self.dataset_id,
            "version": self.version,
            "data_mode": self.data_mode,
            "validation_status": "not_business_validated",
            "manifest_sha256": self.manifest_sha256,
            "status": "unavailable" if not assets else "partial" if issues else "ready",
            "sources": {
                table: {"source_id": source.source_id, "version": source.version, "sha256": source.sha256}
                for table, source in self.sources.items()
            },
            "counts": counts,
            "received_counts": dict(self.received_counts),
            "coverage": {
                "assets_with_coordinates": with_coordinates,
                "assets_without_coordinates": assets - with_coordinates,
                "assets_with_monitoring": with_monitoring,
                "assets_without_monitoring": assets - with_monitoring,
            },
            "issues": [
                {"table": issue.table, "code": issue.code, "row": issue.row, "field": issue.field}
                for issue in issues
            ],
        }


def _file_bytes(
    directory: Path, directory_fd: int | None, name: str, limit: int, table: str | None = None
) -> bytes:
    path = directory / name
    try:
        if path.is_symlink():
            raise IntakeError("file_unavailable", table=table)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        fd = os.open(name, flags, dir_fd=directory_fd) if directory_fd is not None else os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise IntakeError("file_unavailable", table=table)
            if info.st_size > limit:
                raise IntakeError("file_too_large", table=table)
            raw = stream.read(limit + 1)
    except OSError:
        raise IntakeError("file_unavailable", table=table) from None
    if len(raw) > limit:
        raise IntakeError("file_too_large", table=table)
    return raw


def _unique_fields(pairs: list[tuple[str, object]]) -> dict:
    fields = dict(pairs)
    if len(fields) != len(pairs):
        raise ValueError("duplicate manifest field")
    return fields


def _read_manifest(raw: bytes) -> Manifest:
    try:
        data = Manifest.model_validate(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields))
    except (ValueError, RecursionError):
        raise IntakeError("invalid_manifest") from None
    if data.data_mode == "synthetic" and not data.dataset_id.startswith("syn-gas-"):
        raise IntakeError("invalid_manifest")
    return data


def _check_approval(directory: Path, manifest: Manifest, digest: str, approval: ApprovedPackage | None) -> None:
    if approval is None:
        if manifest.data_mode != "synthetic":
            raise IntakeError("approval_required")
        return
    if manifest.data_mode != "real_authorized":
        raise IntakeError("approval_mismatch")
    try:
        approved_directory = approval.directory.resolve(strict=True)
    except (OSError, RuntimeError):
        raise IntakeError("approval_mismatch") from None
    if (
        approved_directory != directory
        or approval.dataset_id != manifest.dataset_id
        or approval.manifest_sha256 != digest
        or not approval.approval_ref.strip()
    ):
        raise IntakeError("approval_mismatch")
    if any(os.path.lexists(parent / ".git") for parent in (directory, *directory.parents)):
        raise IntakeError("real_source_in_git")


def _read_rows(table: str, raw: bytes) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise IntakeError("invalid_csv", table=table)
        rows = csv.reader(StringIO(text, newline=""), strict=True)
        if tuple(next(rows, ())) != HEADERS[table]:
            raise IntakeError("invalid_header", table=table)
        records = []
        for row, values in enumerate(rows, start=1):
            if len(values) != len(HEADERS[table]):
                raise IntakeError("invalid_csv", table=table, row=row)
            records.append(dict(zip(HEADERS[table], values, strict=True)))
        return records
    except (UnicodeError, csv.Error):
        raise IntakeError("invalid_csv", table=table) from None


def read_package(path: Path, *, approval: ApprovedPackage | None = None) -> IntakeSnapshot:
    """Validate fixed local files, leaving every source unchanged.

    A real package requires an independent exact grant. No grant is loaded from
    the package or inferred from its declared mode.
    """

    path = Path(path)
    try:
        if path.is_symlink():
            raise IntakeError("missing_directory")
        directory = path.resolve(strict=True)
        if not directory.is_dir():
            raise IntakeError("missing_directory")
        directory_fd = None
        if os.open in os.supports_dir_fd:
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_fd = os.open(directory, flags)
    except IntakeError:
        raise
    except (OSError, RuntimeError):
        raise IntakeError("missing_directory") from None

    try:
        manifest_raw = _file_bytes(directory, directory_fd, "manifest.json", MAX_MANIFEST_BYTES)
        digest = sha256(manifest_raw).hexdigest()
        manifest = _read_manifest(manifest_raw)
        _check_approval(directory, manifest, digest, approval)
        tables = {}
        for table, metadata in sorted(manifest.sources.items()):
            raw = _file_bytes(directory, directory_fd, f"{table}.csv", MAX_TABLE_BYTES, table)
            if sha256(raw).hexdigest() != metadata.sha256:
                raise IntakeError("hash_mismatch", table=table)
            source = SourceRef(metadata.source_id, metadata.version, metadata.sha256)
            tables[table] = (source, _read_rows(table, raw))
    finally:
        if directory_fd is not None:
            os.close(directory_fd)

    return IntakeSnapshot(
        dataset_id=manifest.dataset_id,
        version=manifest.version,
        data_mode=manifest.data_mode,
        manifest_sha256=digest,
        sources={table: item[0] for table, item in tables.items()},
        received_counts={table: len(tables[table][1]) if table in tables else 0 for table in HEADERS},
        rows=normalize_rows(tables),
        approval_ref=approval.approval_ref if approval and manifest.data_mode == "real_authorized" else None,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="校验合成燃气数据包并输出脱敏质量报告")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        report = read_package(args.directory).quality_report()
    except IntakeError as exc:
        print(json.dumps({"status": "rejected", "code": exc.code, "table": exc.table, "row": exc.row}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
