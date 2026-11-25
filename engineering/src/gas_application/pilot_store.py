"""Owner-only SQLite storage and separate authorization registry for a gas pilot."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import stat
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from .data_package import Digest, Identifier, IntakeError, _file_bytes, _unique_fields
from .pilot_contract import SYNTHETIC_PREFIX, PilotGrant, PilotIdentity, PilotSnapshot, UtcTime
from .pilot_intake import PilotInspection, package_grant
from .pilot_quality import PilotQualityReport, PilotRejectedQuality, quality_summary

REGISTRY_LIMIT = 128 * 1024
GRANTS = TypeAdapter(tuple[PilotGrant, ...])
OPERATOR_ID = TypeAdapter(Identifier)

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS datasets (
        dataset_id TEXT NOT NULL, version TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        entry TEXT NOT NULL, approval_ref TEXT, quality_report TEXT NOT NULL, snapshot TEXT NOT NULL,
        PRIMARY KEY (dataset_id, version)
    )""",
    """CREATE TABLE IF NOT EXISTS rejections (
        receipt INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, version TEXT NOT NULL, manifest_sha256 TEXT NOT NULL,
        at TEXT NOT NULL, report TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS dataset_heads (
        dataset_id TEXT PRIMARY KEY, version TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS admission_audit (
        receipt INTEGER PRIMARY KEY, dataset_id TEXT NOT NULL, version TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL,
        os_uid INTEGER NOT NULL, at TEXT NOT NULL, previous_head TEXT, reason TEXT NOT NULL
    )""",
)


class PilotDatasetEntry(PilotIdentity):
    imported_at: UtcTime
    quality_status: Literal["ready", "partial"]
    pipes: int
    sensors: int
    observations: int
    imported_by: Identifier | None = None
    admission_state: Literal["admitted", "quarantined"] = "admitted"
    grant_scope_sha256: Digest | None = None
    preferred: bool = False


def _private(path: Path, *, directory: bool = False) -> None:
    try:
        info = path.lstat()
    except OSError:
        raise IntakeError("private_store_unavailable") from None
    correct_type = stat.S_ISDIR if directory else stat.S_ISREG
    if not correct_type(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise IntakeError("private_store_permissions")


class PilotStore:
    def __init__(self, directory: Path):
        if not directory.is_absolute() or directory.is_symlink():
            raise IntakeError("private_store_path")
        self.root = directory.resolve()
        _private(self.root, directory=True)
        if any(os.path.lexists(parent / ".git") for parent in (self.root, *self.root.parents)):
            raise IntakeError("private_store_in_git")
        self.database = self.root / "pilot.sqlite"

    def grants(self) -> tuple[PilotGrant, ...]:
        path = self.root / "approvals.json"
        if not os.path.lexists(path):
            return ()
        _private(path)
        try:
            raw = _file_bytes(self.root, None, path.name, REGISTRY_LIMIT)
            grants = GRANTS.validate_python(json.loads(raw, object_pairs_hook=_unique_fields))
        except (ValueError, RecursionError):
            raise IntakeError("invalid_approval_registry") from None
        if len(grants) > 128:
            raise IntakeError("invalid_approval_registry")
        return grants

    def approved(self, identity: PilotIdentity, approval_ref: str | None) -> PilotGrant | None:
        if identity.data_mode == "synthetic":
            return None
        if not approval_ref:
            raise IntakeError("approval_required")
        return package_grant(identity, self.grants(), approval_ref=approval_ref)

    @contextmanager
    def _open(self, *, write: bool = False):
        try:
            if write and not os.path.lexists(self.database):
                try:
                    fd = os.open(self.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    os.close(fd)
                except FileExistsError:
                    pass
            _private(self.database)
            uri = self.database.as_uri()
            with closing(sqlite3.connect(uri + ("?mode=rw" if write else "?mode=ro"), uri=True, timeout=5)) as connection:
                connection.execute("PRAGMA trusted_schema=OFF")
                if write:
                    with connection:
                        connection.execute("BEGIN IMMEDIATE")
                        for statement in _SCHEMA:
                            connection.execute(statement)
                        yield connection
                else:
                    yield connection
        except (sqlite3.Error, ValidationError, OSError, json.JSONDecodeError):
            raise IntakeError("private_store_unavailable") from None

    @staticmethod
    def _head(db: sqlite3.Connection, dataset_id: str) -> str | None:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_heads'").fetchone()
        if not table:
            return None
        head = db.execute("SELECT version FROM dataset_heads WHERE dataset_id=?", (dataset_id,)).fetchone()
        return head[0] if head else None

    @staticmethod
    def _set_head(db: sqlite3.Connection, dataset_id: str, version: str | None) -> None:
        if version is None:
            db.execute("DELETE FROM dataset_heads WHERE dataset_id=?", (dataset_id,))
        else:
            db.execute("INSERT INTO dataset_heads(dataset_id,version) VALUES (?,?) "
                       "ON CONFLICT(dataset_id) DO UPDATE SET version=excluded.version", (dataset_id, version))

    @staticmethod
    def _actor(actor: str | None, grant: PilotGrant | None) -> str:
        if grant is not None and (actor is None or actor not in grant.import_operators):
            raise IntakeError("import_operator_not_approved")
        try:
            return OPERATOR_ID.validate_python(actor if actor is not None else f"linux-uid-{os.geteuid()}")
        except ValidationError:
            raise IntakeError("invalid_import_operator") from None

    @staticmethod
    def _audit(db, identity, action, actor, previous, reason):
        db.execute(
            "INSERT INTO admission_audit(dataset_id,version,manifest_sha256,action,actor,os_uid,at,previous_head,reason)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (identity.dataset_id, identity.version, identity.manifest_sha256, action, actor, os.geteuid(),
             datetime.now(UTC).isoformat(), previous, reason),
        )

    @staticmethod
    def _get(db, dataset_id: str, version: str):
        row = db.execute("SELECT entry,approval_ref FROM datasets WHERE dataset_id=? AND version=?",
                         (dataset_id, version)).fetchone()
        return (PilotDatasetEntry.model_validate_json(row[0]), row[1]) if row else None

    def prepare(
        self, inspection: PilotInspection, *, actor: str | None = None, readmit: bool = False,
    ) -> dict:
        identity = inspection.identity
        grant = self.approved(identity, inspection.approval_ref)
        if grant is not None and inspection.grant_scope_sha256 != grant.scope_digest():
            raise IntakeError("approved_scope_changed")
        operator = self._actor(actor, grant)
        at = datetime.now(UTC)
        with self._open(write=True) as db:
            previous = self._head(db, identity.dataset_id)
            if inspection.snapshot is None:
                summary = quality_summary(inspection.report, limit=4000).model_dump(mode="json")
                receipt = db.execute(
                    "INSERT INTO rejections(dataset_id, version, manifest_sha256, at, report) VALUES (?, ?, ?, ?, ?)",
                    (identity.dataset_id, identity.version, identity.manifest_sha256, at.isoformat(),
                     json.dumps({**summary, "data_mode": identity.data_mode,
                                 "approval_ref": inspection.approval_ref,
                                 "grant_scope_sha256": inspection.grant_scope_sha256}, ensure_ascii=False)),
                )
                self._audit(db, identity, "rejected", operator, previous, "quality_validation")
                return {"status": "rejected", "receipt": receipt.lastrowid}
            key = (identity.dataset_id, identity.version)
            current = self._get(db, *key)
            if current is not None:
                if current[0].manifest_sha256 != identity.manifest_sha256:
                    raise IntakeError("dataset_version_conflict")
                if not readmit:
                    if (current[0].admission_state != "admitted"
                        or (grant is not None and current[0].grant_scope_sha256 != grant.scope_digest())):
                        raise IntakeError("dataset_requires_readmission")
                    return {"status": "already_imported"}
            elif readmit:
                raise IntakeError("dataset_version_not_found")
            counts = inspection.report["structural_rows"]
            entry = PilotDatasetEntry(
                **identity.model_dump(), imported_at=at, imported_by=operator, quality_status=inspection.report["status"],
                grant_scope_sha256=grant.scope_digest() if grant else None,
                pipes=counts["pipes"], sensors=counts["sensors"], observations=counts["observations"],
            )
            payload = (entry.model_dump_json(), inspection.approval_ref,
                       json.dumps(inspection.report, ensure_ascii=False), inspection.snapshot.model_dump_json())
            if current:
                db.execute("UPDATE datasets SET entry=?,approval_ref=?,quality_report=?,snapshot=? WHERE dataset_id=? AND version=?",
                           (*payload, *key))
            else:
                db.execute(
                    "INSERT INTO datasets(dataset_id,version,manifest_sha256,entry,approval_ref,quality_report,snapshot)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?)", (*key, identity.manifest_sha256, *payload),
                )
            self._set_head(db, identity.dataset_id, identity.version)
            action = "readmitted" if current else "imported"
            self._audit(db, identity, action, operator, previous, "verified_ingest")
            return {"status": action}

    def change_version(self, dataset_id: str, version: str, *, action: Literal["quarantine", "rollback"],
                       actor: str, reason: str) -> dict:
        if not reason.strip() or len(reason) > 240 or any(ord(char) < 32 for char in reason):
            raise IntakeError("invalid_admission_reason")
        with self._open(write=True) as db:
            found = self._get(db, dataset_id, version)
            if found is None:
                raise IntakeError("dataset_version_not_found")
            entry, ref = found
            previous = self._head(db, dataset_id)
            if action == "rollback":
                grant = self.approved(entry, ref)
                operator = self._actor(actor, grant)
                self.ensure_admitted(entry, grant)
                self._set_head(db, dataset_id, version)
            else:
                operator = self._actor(actor, None)
                entry = entry.model_copy(update={"admission_state": "quarantined"})
                db.execute("UPDATE datasets SET entry=? WHERE dataset_id=? AND version=?",
                           (entry.model_dump_json(), dataset_id, version))
                if previous == version:
                    self._set_head(db, dataset_id, None)
            self._audit(db, entry, action, operator, previous, reason.strip())
            return {"status": action, "dataset_id": dataset_id, "version": version,
                    "preferred_version": self._head(db, dataset_id)}

    @staticmethod
    def ensure_admitted(entry: PilotDatasetEntry, grant: PilotGrant | None) -> None:
        if entry.admission_state == "quarantined":
            raise IntakeError("dataset_quarantined")
        if grant is not None and entry.grant_scope_sha256 != grant.scope_digest():
            raise IntakeError("dataset_requires_readmission")

    def entries(self, datasets: frozenset[str]) -> list[tuple[PilotDatasetEntry, str | None]]:
        if not datasets or not os.path.lexists(self.database):
            return []
        authorized = []
        with self._open() as db:
            for dataset_id in sorted(datasets):
                preferred = self._head(db, dataset_id)
                rows = db.execute(
                    "SELECT entry, approval_ref FROM datasets WHERE dataset_id=? ORDER BY version", (dataset_id,),
                )
                for raw, ref in rows:
                    entry = PilotDatasetEntry.model_validate_json(raw)
                    authorized.append((entry.model_copy(update={"preferred": entry.version == preferred}), ref))
        return authorized

    def entry(self, dataset_id: Identifier, version: Identifier) -> tuple[PilotDatasetEntry, str | None] | None:
        if not os.path.lexists(self.database):
            return None
        with self._open() as db:
            found = self._get(db, dataset_id, version)
            if found is None:
                return None
            entry, ref = found
            return entry.model_copy(update={"preferred": self._head(db, dataset_id) == version}), ref

    def rejected(self, datasets: frozenset[str]) -> list[tuple[PilotRejectedQuality, str | None, str | None]]:
        candidates = []
        if not datasets or not os.path.lexists(self.database):
            return candidates
        with self._open() as db:
            for dataset_id in sorted(datasets):
                rows = db.execute(
                    "SELECT receipt,version,manifest_sha256,at,report FROM rejections "
                    "WHERE dataset_id=? ORDER BY receipt DESC LIMIT 10", (dataset_id,),
                )
                for receipt, version, digest, at, raw in rows:
                    report = json.loads(raw)
                    mode = report.get("data_mode")
                    if mode is None and dataset_id.startswith(SYNTHETIC_PREFIX):
                        mode = "synthetic"
                    if mode is None:
                        continue
                    rejected = PilotRejectedQuality(
                        dataset_id=dataset_id, version=version, data_mode=mode, manifest_sha256=digest,
                        receipt=receipt, rejected_at=at, quality=quality_summary(report),
                    )
                    candidates.append((rejected, report.get("approval_ref"), report.get("grant_scope_sha256")))
        return sorted(candidates, key=lambda row: row[0].receipt, reverse=True)

    def quality(self, identity: PilotIdentity) -> PilotQualityReport:
        with self._open() as db:
            row = db.execute("SELECT quality_report FROM datasets WHERE dataset_id=? AND version=? AND manifest_sha256=?",
                             (identity.dataset_id, identity.version, identity.manifest_sha256)).fetchone()
            if not row:
                raise IntakeError("private_store_unavailable")
            return quality_summary(json.loads(row[0]))

    def snapshot(self, identity: PilotIdentity) -> PilotSnapshot:
        with self._open() as db:
            row = db.execute("SELECT snapshot,entry FROM datasets WHERE dataset_id=? AND version=? AND manifest_sha256=?",
                             (identity.dataset_id, identity.version, identity.manifest_sha256)).fetchone()
            if not row:
                raise IntakeError("private_store_unavailable")
            current = PilotDatasetEntry.model_validate_json(row[1])
            if current.admission_state != "admitted":
                raise IntakeError("dataset_quarantined")
            if isinstance(identity, PilotDatasetEntry) and current.grant_scope_sha256 != identity.grant_scope_sha256:
                raise IntakeError("dataset_requires_readmission")
            snapshot = PilotSnapshot.model_validate_json(row[0])
            if any(getattr(snapshot, name) != getattr(identity, name) for name in PilotIdentity.model_fields):
                raise IntakeError("private_store_identity_mismatch")
            return snapshot
