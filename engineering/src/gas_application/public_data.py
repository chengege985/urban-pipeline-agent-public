"""Read-only adapters for the isolated PHMSA and Dryad gas datasets.

The raw archives are provisioned outside Git. This module validates the
provisioned receipt before parsing, keeps the two dataset identities separate,
and exposes bounded quality summaries and evidence-backed queries only.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import Principal, resolve_principal
from .gaslib import GASLIB_DATASET_ID, GasLibService, gaslib_router


PUBLIC_CORPUS_ID = "gas-public-20261124"
PHMSA_DATASET_ID = "US-PHMSA-INCIDENT"
DRYAD_DATASET_ID = "US-DRYAD-CH4"
PUBLIC_DATA_ROOT_ENV = "GAS_PUBLIC_DATA_ROOT"

PhmsaCategory = Literal[
    "gas_distribution",
    "gas_transmission_gathering",
    "lng",
    "type_r_gas_gathering",
]


class PublicStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PublicDatasetSummary(PublicStrictModel):
    dataset_id: str
    version: str
    title: str
    source_uri: str
    source_kind: str
    data_kind: str
    geo_scope: str
    license_status: str
    raw_archive_sha256: str
    quality_status: Literal["checked", "partial", "unavailable"]
    ingest_status: Literal["raw_only", "candidate_ready", "unavailable"]
    record_count: int | None = None
    file_count: int | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PublicCatalogResponse(PublicStrictModel):
    status: Literal["ready", "partial", "unavailable"]
    corpus_id: str
    datasets: list[PublicDatasetSummary] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PhmsaFileQuality(PublicStrictModel):
    category: PhmsaCategory
    path: str
    archive_sha256: str
    record_count: int
    column_count: int
    missing_record_id: int
    missing_year: int
    parse_errors: int
    evidence_ref: str


class PhmsaQualityResponse(PublicStrictModel):
    dataset_id: Literal["US-PHMSA-INCIDENT"] = PHMSA_DATASET_ID
    version: str
    status: Literal["checked", "partial", "unavailable"]
    files: list[PhmsaFileQuality] = Field(default_factory=list)
    total_records: int
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PhmsaIncidentPage(PublicStrictModel):
    dataset_id: Literal["US-PHMSA-INCIDENT"] = PHMSA_DATASET_ID
    version: str
    status: Literal["succeeded", "not_found", "unavailable"]
    category: PhmsaCategory | None = None
    total: int
    items: list[dict[str, object]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class DryadExperimentSummary(PublicStrictModel):
    experiment_id: str
    filename: str
    member_sha256: str
    member_md5: str
    bytes: int
    record_count: int
    columns: list[str]
    missing_counts: dict[str, int] = Field(default_factory=dict)
    sample_rows: list[dict[str, str]] = Field(default_factory=list)
    evidence_ref: str


class DryadQualityResponse(PublicStrictModel):
    dataset_id: Literal["US-DRYAD-CH4"] = DRYAD_DATASET_ID
    version: str
    status: Literal["checked", "partial", "unavailable"]
    file_count: int
    total_records: int
    columns_consistent: bool
    experiments: list[DryadExperimentSummary] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class PublicEvidenceReport(PublicStrictModel):
    report_id: str
    dataset_id: str
    version: str
    generated_at: datetime
    status: Literal["checked", "partial", "unavailable"]
    source_kind: str
    findings: list[str] = Field(default_factory=list)
    quality_findings: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    report_sha256: str


class PublicAgentResult(PublicStrictModel):
    status: Literal["succeeded", "partial", "not_found", "unavailable"]
    dataset_id: str
    tool_name: Literal["phmsa.incident.query", "dryad.experiment.query", "public.report.query"]
    facts: list[str] = Field(default_factory=list)
    data: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


_PHMSA_CATEGORY_BY_DIR: dict[str, PhmsaCategory] = {
    "GAS-DISTRIBUTION": "gas_distribution",
    "GAS-TRANSMISSION-GATHERING": "gas_transmission_gathering",
    "LNG": "lng",
    "TYPE-R-GAS-GATHERING": "type_r_gas_gathering",
}
_PHMSA_RECORD_KEYS = (
    "REPORT_NUMBER", "REPORT_ID", "RPTID", "UNIQUE_ID", "REPORT_ID",
)
_PHMSA_YEAR_KEYS = ("IYEAR", "REPORT_RECEIVED_YEAR", "REPORT_RECEIVED_DATE", "LOCAL_DATETIME", "IDATE", "ACCIDENT_DATE")
_PHMSA_DATE_KEYS = ("LOCAL_DATETIME", "INCIDENT_DATE", "ACCIDENT_DATE", "IDATE", "NOTIF_DATE")
_PHMSA_OPERATOR_KEYS = ("NAME", "OPERATOR_NAME", "OPERATOR_ID", "OPERATOR_CODE")
_PHMSA_STATE_KEYS = ("LOCATION_STATE_ABBREVIATION", "INCIDENT_STATE", "ACSTATE", "ACCIDENT_STATE", "OPSTATE")
_PHMSA_CITY_KEYS = ("LOCATION_CITY_NAME", "INCIDENT_CITY", "ACCITY", "ACCIDENT_CITY", "OPCITY")
_PHMSA_COUNTY_KEYS = ("LOCATION_COUNTY_NAME", "INCIDENT_COUNTY", "ACCOUNTY", "ACCIDENT_COUNTY", "OPCOUNTY")
_PHMSA_CAUSE_KEYS = ("CAUSE_TEXT", "CAUSE_IDENTIFIER", "CAUSE", "GEN_CAUSE_TXT", "PRIMARY_CAUSE_TEXT", "CAUS")
_PHMSA_COMMODITY_KEYS = ("COMMODITY_RELEASED_TYPE", "MATERIAL_LEAKED", "MATERIAL_LEAKED_OTHER", "COMMODITY", "COMM")
_PHMSA_LAT_KEYS = ("LOCATION_LATITUDE", "LATITUDE", "FACILITY_LATITUDE")
_PHMSA_LON_KEYS = ("LOCATION_LONGITUDE", "LONGITUDE", "FACILITY_LONGITUDE")


def _first(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _year(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(?:19|20)\d{2}", value)
    return int(match.group(0)) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _canonical_sha(payload: object) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class GasPublicDataService:
    """Read-only service for raw public research and historical archives."""

    def __init__(self, root: str | Path | None = None):
        configured = root if root is not None else os.getenv(PUBLIC_DATA_ROOT_ENV, "")
        self.root = Path(configured).expanduser().resolve() if configured else None
        self._phmsa: dict[str, object] | None = None
        self._dryad: dict[str, object] | None = None
        self._errors: dict[str, str] = {}
        self.gaslib_service = GasLibService(root=self.root)

    def _authorize(self, principal: Principal) -> None:
        if not principal.authenticated:
            raise HTTPException(401, "未完成服务端身份认证")
        if principal.scope != "internal":
            raise HTTPException(403, "公共燃气研究数据仅允许内部主体读取")
        if PUBLIC_CORPUS_ID not in principal.corpora:
            raise HTTPException(403, "当前主体无权读取燃气公共研究数据")

    def _required_root(self) -> Path:
        if self.root is None:
            raise RuntimeError("GAS_PUBLIC_DATA_ROOT未配置")
        return self.root

    def _receipt(self, filename: str) -> dict[str, object]:
        path = self._required_root() / "receipts" / filename
        return json.loads(path.read_text(encoding="utf-8"))

    def _archive_path(self, relative: str, base_dir: str = "") -> Path:
        root = self._required_root()
        path = (root / "raw" / base_dir / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise RuntimeError("原始数据路径不存在或越界")
        return path

    def _verify_receipt_archive(self, receipt: dict[str, object], relative: str,
                                base_dir: str = "") -> tuple[Path, str]:
        path = self._archive_path(relative, base_dir)
        expected = next((row for row in receipt.get("rows", receipt.get("member_rows", []))
                         if isinstance(row, dict) and row.get("path") == relative), None)
        if expected is None:
            expected_sha = receipt.get("sha256") if receipt.get("path") == relative else None
        else:
            expected_sha = expected.get("sha256")
        actual = _sha256(path)
        if not isinstance(expected_sha, str) or actual != expected_sha:
            raise RuntimeError("原始数据哈希与接收回执不一致")
        return path, actual

    def _load_phmsa(self) -> dict[str, object]:
        if self._phmsa is not None:
            return self._phmsa
        try:
            receipt = self._receipt("phmsa_manual_download_20261124.json")
            files: list[PhmsaFileQuality] = []
            records: list[dict[str, object]] = []
            for row in receipt.get("rows", []):
                if not isinstance(row, dict):
                    continue
                category = _PHMSA_CATEGORY_BY_DIR.get(str(row.get("category", "")))
                relative = str(row.get("path", ""))
                if category is None or not relative:
                    continue
                path, archive_sha = self._verify_receipt_archive(receipt, relative, "US-PHMSA")
                with zipfile.ZipFile(path) as archive:
                    if archive.testzip() is not None:
                        raise RuntimeError("PHMSA ZIP CRC校验失败")
                    text_names = [name for name in archive.namelist()
                                  if name.lower().endswith(".txt") and "field" not in name.lower()]
                    if len(text_names) != 1:
                        raise RuntimeError("PHMSA ZIP数据文件数量不唯一")
                    text = archive.read(text_names[0]).decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(text, newline=""), delimiter="\t")
                columns = list(reader.fieldnames or [])
                missing_id = 0
                missing_year = 0
                parse_errors = 0
                count = 0
                for source_row, raw in enumerate(reader, 2):
                    count += 1
                    row_map = {str(k): ("" if v is None else str(v)) for k, v in raw.items() if k is not None}
                    record_id = _first(row_map, _PHMSA_RECORD_KEYS)
                    year_value = _first(row_map, _PHMSA_YEAR_KEYS)
                    year = _year(year_value)
                    if record_id is None:
                        missing_id += 1
                    if year is None:
                        missing_year += 1
                    try:
                        item = {
                            "record_id": record_id,
                            "year": year,
                            "event_date": _first(row_map, _PHMSA_DATE_KEYS),
                            "operator": _first(row_map, _PHMSA_OPERATOR_KEYS),
                            "state": _first(row_map, _PHMSA_STATE_KEYS),
                            "city": _first(row_map, _PHMSA_CITY_KEYS),
                            "county": _first(row_map, _PHMSA_COUNTY_KEYS),
                            "cause": _first(row_map, _PHMSA_CAUSE_KEYS),
                            "commodity": _first(row_map, _PHMSA_COMMODITY_KEYS),
                            "latitude": _first(row_map, _PHMSA_LAT_KEYS),
                            "longitude": _first(row_map, _PHMSA_LON_KEYS),
                            "source_file": relative,
                            "source_row": source_row,
                            "source_category": category,
                            "source_archive_sha256": archive_sha,
                            "data_mode": "historical_us",
                            "evidence_ref": f"phmsa:{relative}:row:{source_row}:sha256:{archive_sha}",
                        }
                        records.append(item)
                    except (AttributeError, TypeError, ValueError):
                        parse_errors += 1
                files.append(PhmsaFileQuality(
                    category=category, path=relative, archive_sha256=archive_sha,
                    record_count=count, column_count=len(columns), missing_record_id=missing_id,
                    missing_year=missing_year, parse_errors=parse_errors,
                    evidence_ref=f"phmsa:archive:{relative}:sha256:{archive_sha}",
                ))
            self._phmsa = {"version": "20261124", "records": records, "files": files}
            return self._phmsa
        except (OSError, KeyError, RuntimeError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            self._errors[PHMSA_DATASET_ID] = type(exc).__name__
            raise

    def _load_dryad(self) -> dict[str, object]:
        if self._dryad is not None:
            return self._dryad
        try:
            receipt = self._receipt("dryad_manual_download_20261124.json")
            relative = str(receipt["path"])
            path, archive_sha = self._verify_receipt_archive({"path": relative, "sha256": receipt["sha256"]}, relative)
            with zipfile.ZipFile(path) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("Dryad ZIP CRC校验失败")
                rows: list[DryadExperimentSummary] = []
                expected_columns: list[str] | None = None
                for member in archive.infolist():
                    if not member.filename.lower().endswith(".csv"):
                        continue
                    content = archive.read(member.filename)
                    member_sha = hashlib.sha256(content).hexdigest()
                    member_md5 = hashlib.md5(content).hexdigest()
                    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace"), newline=""))
                    columns = list(reader.fieldnames or [])
                    if expected_columns is None:
                        expected_columns = columns
                    missing: Counter[str] = Counter()
                    sample: list[dict[str, str]] = []
                    count = 0
                    for raw in reader:
                        count += 1
                        row = {str(k): ("" if v is None else str(v)) for k, v in raw.items() if k is not None}
                        for key, value in row.items():
                            if not value.strip() or value.strip().lower() == "nan":
                                missing[key] += 1
                        if len(sample) < 3:
                            sample.append(row)
                    experiment_id = Path(member.filename).stem.split(".")[0]
                    rows.append(DryadExperimentSummary(
                        experiment_id=experiment_id, filename=member.filename,
                        member_sha256=member_sha, member_md5=member_md5, bytes=len(content),
                        record_count=count, columns=columns, missing_counts=dict(missing),
                        sample_rows=sample,
                        evidence_ref=f"dryad:{member.filename}:sha256:{member_sha}",
                    ))
            self._dryad = {"version": str(receipt.get("version", "20231105")), "archive_sha": archive_sha,
                           "rows": rows, "columns": expected_columns or []}
            return self._dryad
        except (OSError, KeyError, RuntimeError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            self._errors[DRYAD_DATASET_ID] = type(exc).__name__
            raise

    def catalog(self, principal: Principal) -> PublicCatalogResponse:
        self._authorize(principal)
        summaries: list[PublicDatasetSummary] = []
        limitations = [
            "公开历史和实验数据保持独立身份，不代表重庆运营管网。",
            "本服务只返回确定性摘要和证据，不输出压力、流量、风险或处置结论。",
        ]
        try:
            phmsa = self._load_phmsa()
            files = phmsa["files"]
            assert isinstance(files, list)
            summaries.append(PublicDatasetSummary(
                dataset_id=PHMSA_DATASET_ID, version=str(phmsa["version"]), title="PHMSA美国历史燃气事故档案",
                source_uri="https://www.phmsa.dot.gov/data-and-statistics/pipeline/distribution-transmission-gathering-lng-and-liquid-accident-and-incident-data",
                source_kind="public_us_regulatory_archive", data_kind="official_incidents", geo_scope="United States",
                license_status="official_public_archive_pending_republication_review", raw_archive_sha256="multiple",
                quality_status="checked", ingest_status="candidate_ready", record_count=sum(x.record_count for x in files),
                file_count=len(files), evidence_refs=[x.evidence_ref for x in files], limitations=["美国历史数据，不是重庆当前事件"]
            ))
        except Exception:
            limitations.append("PHMSA原始档案未能通过接收回执或解析校验。")
        try:
            dryad = self._load_dryad()
            rows = dryad["rows"]
            assert isinstance(rows, list)
            summaries.append(PublicDatasetSummary(
                dataset_id=DRYAD_DATASET_ID, version=str(dryad["version"]), title="Dryad地下天然气释放甲烷与气象实验",
                source_uri="https://datadryad.org/dataset/doi:10.5061/dryad.ncjsxkt15",
                source_kind="public_research_experiment", data_kind="experimental_measurement", geo_scope="Colorado METEC, US",
                license_status="CC0-1.0", raw_archive_sha256=str(dryad["archive_sha"]), quality_status="checked",
                ingest_status="candidate_ready", record_count=sum(x.record_count for x in rows), file_count=len(rows) + 1,
                evidence_refs=[x.evidence_ref for x in rows], limitations=["甲烷和气象实验，不是管网压力流量"]
            ))
        except Exception:
            limitations.append("Dryad原始档案未能通过接收回执或解析校验。")
        gaslib_expected = (self.root / "raw/DE-GASLIB").exists() or (self.root / "receipts/gaslib_quality_acceptance_20261125.json").exists()
        if gaslib_expected:
            try:
                gaslib_instances = self.gaslib_service.catalog(principal)
                if gaslib_instances:
                    summaries.append(PublicDatasetSummary(
                        dataset_id=GASLIB_DATASET_ID, version="20261124",
                        title="GasLib公开燃气网络XML基准",
                        source_uri="https://gaslib.zib.de/data.html",
                        source_kind="public_research_benchmark", data_kind="historical_network",
                        geo_scope="Germany/research benchmark", license_status="CC-BY-3.0",
                        raw_archive_sha256="multiple", quality_status="checked",
                        ingest_status="candidate_ready", file_count=len(gaslib_instances),
                        evidence_refs=[f"gaslib:archive:{item.archive_path}:sha256:{item.archive_sha256}" for item in gaslib_instances],
                        limitations=["公开研究基准，不代表重庆实际管网。"],
                    ))
            except Exception:
                limitations.append("GasLib原始ZIP未能通过接收回执或哈希校验。")
        expected_catalog_count = 3 if gaslib_expected else 2
        status: Literal["ready", "partial", "unavailable"] = "ready" if len(summaries) == expected_catalog_count else "partial" if summaries else "unavailable"
        return PublicCatalogResponse(status=status, corpus_id=PUBLIC_CORPUS_ID, datasets=summaries, limitations=limitations)

    def phmsa_quality(self, principal: Principal) -> PhmsaQualityResponse:
        self._authorize(principal)
        try:
            data = self._load_phmsa()
        except Exception:
            return PhmsaQualityResponse(version="20261124", status="unavailable", files=[], total_records=0,
                                        limitations=["PHMSA原始档案不可用或回执校验失败。"])
        files = data["files"]
        assert isinstance(files, list)
        status = "checked" if all(item.parse_errors == 0 for item in files) else "partial"
        return PhmsaQualityResponse(version=str(data["version"]), status=status, files=files,
                                    total_records=sum(item.record_count for item in files),
                                    evidence_refs=[item.evidence_ref for item in files],
                                    limitations=["记录来自美国历史档案，跨年份字段合同不同。", "缺失值保留为null，不以零替代。"])

    def phmsa_incidents(self, principal: Principal, *, category: PhmsaCategory | None = None,
                        year_from: int | None = None, year_to: int | None = None,
                        state: str | None = None, limit: int = 100, offset: int = 0) -> PhmsaIncidentPage:
        self._authorize(principal)
        try:
            data = self._load_phmsa()
        except Exception:
            return PhmsaIncidentPage(version="20261124", status="unavailable", category=category, total=0,
                                     limitations=["PHMSA原始档案不可用或回执校验失败。"])
        records = data["records"]
        assert isinstance(records, list)
        filtered = [row for row in records if (category is None or row["source_category"] == category)
                    and (year_from is None or (row["year"] is not None and row["year"] >= year_from))
                    and (year_to is None or (row["year"] is not None and row["year"] <= year_to))
                    and (state is None or str(row.get("state") or "").upper() == state.upper())]
        page = filtered[offset:offset + limit]
        return PhmsaIncidentPage(version=str(data["version"]), status="succeeded" if page else "not_found",
                                 category=category, total=len(filtered), items=page,
                                 evidence_refs=list(dict.fromkeys(str(row["evidence_ref"]) for row in page)),
                                 limitations=["PHMSA数据是美国历史事故，不可映射为重庆当前事件。", "字段合同按历史文件版本变化。"])

    def dryad_quality(self, principal: Principal) -> DryadQualityResponse:
        self._authorize(principal)
        try:
            data = self._load_dryad()
        except Exception:
            return DryadQualityResponse(version="20231105", status="unavailable", file_count=0, total_records=0,
                                        columns_consistent=False, limitations=["Dryad原始档案不可用或回执校验失败。"])
        rows = data["rows"]
        assert isinstance(rows, list)
        columns = data["columns"]
        status = "checked" if rows and all(item.columns == columns for item in rows) else "partial"
        return DryadQualityResponse(version=str(data["version"]), status=status, file_count=len(rows) + 1,
                                    total_records=sum(item.record_count for item in rows),
                                    columns_consistent=all(item.columns == columns for item in rows), experiments=rows,
                                    evidence_refs=[item.evidence_ref for item in rows],
                                    limitations=["实验数据来自Colorado METEC，不含重庆资产关联。", "缺测和NaN保持质量统计，不补零。"])

    def dryad_experiment(self, principal: Principal, experiment_id: str) -> DryadExperimentSummary:
        self._authorize(principal)
        data = self._load_dryad()
        rows = data["rows"]
        assert isinstance(rows, list)
        item = next((row for row in rows if row.experiment_id == experiment_id), None)
        if item is None:
            raise HTTPException(404, "Dryad实验不存在")
        return item

    def report(self, principal: Principal, dataset_id: str) -> PublicEvidenceReport:
        self._authorize(principal)
        if dataset_id == PHMSA_DATASET_ID:
            quality = self.phmsa_quality(principal)
            findings = [f"PHMSA燃气历史档案解析 {quality.total_records} 条记录，覆盖 {len(quality.files)} 个来源文件。"]
            unknowns = ["不支持重庆资产、当前运营状态、实时压力、流量或风险判断。"]
            evidence = quality.evidence_refs
            status = quality.status
            source_kind = "public_us_regulatory_archive"
            version = quality.version
        elif dataset_id == DRYAD_DATASET_ID:
            quality = self.dryad_quality(principal)
            findings = [f"Dryad实验档案解析 {quality.total_records} 条观测，包含 {quality.file_count - 1} 个实验CSV。"]
            unknowns = ["不支持管段资产关联、运营压力、流量、泄漏概率或重庆地理结论。"]
            evidence = quality.evidence_refs
            status = quality.status
            source_kind = "public_research_experiment"
            version = quality.version
        else:
            raise HTTPException(404, "公共燃气数据集不存在")
        base = {"dataset_id": dataset_id, "version": version, "status": status,
                "findings": findings, "quality_findings": ["原件哈希、成员结构和字段解析已完成。"],
                "unknowns": unknowns, "evidence_refs": evidence}
        return PublicEvidenceReport(report_id=f"public-report-{dataset_id}-{version}", generated_at=datetime.now(UTC),
                                    source_kind=source_kind, report_sha256=_canonical_sha(base), **base)

    def agent_query(self, principal: Principal, question: str, tool: str) -> PublicAgentResult:
        self._authorize(principal)
        text = question.strip()
        if tool == "phmsa.incident.query":
            year_values = [int(value) for value in re.findall(r"\b(?:19|20)\d{2}\b", text)]
            state_match = re.search(r"\b([A-Z]{2})\b", text)
            category: PhmsaCategory | None = None
            if "配气" in text or "distribution" in text.lower(): category = "gas_distribution"
            elif "输气" in text or "集输" in text or "transmission" in text.lower(): category = "gas_transmission_gathering"
            elif "LNG" in text.upper(): category = "lng"
            elif "Type R" in text or "R类" in text: category = "type_r_gas_gathering"
            page = self.phmsa_incidents(principal, category=category,
                                        year_from=min(year_values) if year_values else None,
                                        year_to=max(year_values) if len(year_values) > 1 else None,
                                        state=state_match.group(1) if state_match else None, limit=20)
            facts = [f"PHMSA查询返回 {page.total} 条符合条件的美国历史记录。", "结果按历史来源文件和源行保留证据引用。"]
            return PublicAgentResult(status=page.status, dataset_id=PHMSA_DATASET_ID, tool_name=tool,
                                     facts=facts, data=page.model_dump(mode="json"), evidence_refs=page.evidence_refs,
                                     limitations=page.limitations)
        if tool == "dryad.experiment.query":
            match = re.search(r"Experiment[_ -]?(\d)", text, re.I)
            if match:
                item = self.dryad_experiment(principal, f"Experiment_{match.group(1)}")
                data = item.model_dump(mode="json")
                facts = [f"{item.experiment_id} 包含 {item.record_count} 条观测，字段数为 {len(item.columns)}。"]
                return PublicAgentResult(status="succeeded", dataset_id=DRYAD_DATASET_ID, tool_name=tool,
                                         facts=facts, data=data, evidence_refs=[item.evidence_ref],
                                         limitations=["Dryad为甲烷和气象实验数据，不是重庆管网运行数据。"])
            quality = self.dryad_quality(principal)
            return PublicAgentResult(status="succeeded", dataset_id=DRYAD_DATASET_ID, tool_name=tool,
                                     facts=[f"Dryad包含 {quality.file_count - 1} 个实验CSV，共 {quality.total_records} 条观测。"],
                                     data=quality.model_dump(mode="json"), evidence_refs=quality.evidence_refs,
                                     limitations=quality.limitations)
        if tool == "public.report.query":
            dataset_id = PHMSA_DATASET_ID if "PHMSA" in text.upper() or "事故" in text else DRYAD_DATASET_ID
            report = self.report(principal, dataset_id)
            return PublicAgentResult(status="succeeded", dataset_id=dataset_id, tool_name=tool,
                                     facts=report.findings + report.quality_findings, data=report.model_dump(mode="json"),
                                     evidence_refs=report.evidence_refs, limitations=report.unknowns)
        raise HTTPException(422, "公共数据工具不支持")


def gas_public_data_router(service: GasPublicDataService | None = None) -> APIRouter:
    public = service or GasPublicDataService()
    router = APIRouter(prefix="/public-data", tags=["gas-public-data"])

    def principal(request: Request) -> Principal:
        value = resolve_principal(request)
        public._authorize(value)
        return value

    security = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}

    @router.get("/catalog", response_model=PublicCatalogResponse, **security)
    def catalog(request: Request):
        return public.catalog(principal(request))

    @router.get("/phmsa/quality", response_model=PhmsaQualityResponse, **security)
    def phmsa_quality(request: Request):
        return public.phmsa_quality(principal(request))

    @router.get("/phmsa/incidents", response_model=PhmsaIncidentPage, **security)
    def phmsa_incidents(request: Request, category: PhmsaCategory | None = None,
                        year_from: int | None = Query(default=None, ge=1900, le=2100),
                        year_to: int | None = Query(default=None, ge=1900, le=2100),
                        state: str | None = Query(default=None, min_length=2, max_length=2),
                        limit: int = Query(default=100, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        return public.phmsa_incidents(principal(request), category=category, year_from=year_from,
                                      year_to=year_to, state=state, limit=limit, offset=offset)

    @router.get("/dryad/quality", response_model=DryadQualityResponse, **security)
    def dryad_quality(request: Request):
        return public.dryad_quality(principal(request))

    @router.get("/dryad/experiments", response_model=DryadQualityResponse, **security)
    def dryad_experiments(request: Request):
        return public.dryad_quality(principal(request))

    @router.get("/dryad/experiments/{experiment_id}", response_model=DryadExperimentSummary, **security)
    def dryad_experiment(experiment_id: str, request: Request):
        return public.dryad_experiment(principal(request), experiment_id)

    @router.get("/reports/{dataset_id}", response_model=PublicEvidenceReport, **security)
    def report(dataset_id: str, request: Request):
        return public.report(principal(request), dataset_id)

    router.include_router(gaslib_router(public.gaslib_service))
    return router


__all__ = [
    "DRYAD_DATASET_ID", "PHMSA_DATASET_ID", "PUBLIC_CORPUS_ID", "GasPublicDataService",
    "PublicCatalogResponse", "PublicEvidenceReport", "PhmsaIncidentPage", "PhmsaQualityResponse",
    "DryadExperimentSummary", "DryadQualityResponse", "gas_public_data_router",
]
