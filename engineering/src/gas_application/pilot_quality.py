"""Bounded diagnostics for the authorized dashboard without source cell values."""

from __future__ import annotations

from typing import Literal

from .data_package import Identifier
from .pilot_contract import Contract, PilotIdentity, Table, UtcTime


class PilotQualityIssue(Contract):
    table: Table
    code: Identifier
    row: int | None = None
    field: str | None = None
    severity: Literal["error", "warning"]


class PilotQualityCounts(Contract):
    error: int
    warning: int


class PilotFieldGap(Contract):
    table: Table
    fields: tuple[str, ...]


class PilotQualityReport(Contract):
    issue_counts: PilotQualityCounts
    issues: tuple[PilotQualityIssue, ...]
    missing_fields: tuple[PilotFieldGap, ...] = ()
    findings_omitted: int = 0


class PilotRejectedQuality(PilotIdentity):
    receipt: int
    rejected_at: UtcTime
    quality: PilotQualityReport


def quality_summary(report: dict, *, limit: int = 120) -> PilotQualityReport:
    counts = PilotQualityCounts.model_validate(report["issue_counts"])
    gaps = {item["table"]: set(item["fields"]) for item in report.get("missing_fields", ())}
    for table, mapping in report.get("field_preview", {}).items():
        gaps.setdefault(table, set()).update(mapping["missing_fields"])
    issues = report["issues"]
    return PilotQualityReport(
        issue_counts=counts, issues=issues[:limit],
        missing_fields=tuple(PilotFieldGap(table=table, fields=tuple(sorted(fields)))
                             for table, fields in sorted(gaps.items()) if fields),
        findings_omitted=max(0, counts.error + counts.warning - min(limit, len(issues))),
    )
