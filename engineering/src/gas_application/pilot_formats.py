"""Bounded offline readers for operator-mapped sources."""

from __future__ import annotations

import csv
from io import BytesIO, StringIO
import json
from typing import NamedTuple
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from .data_package import IntakeError, _unique_fields
from .pilot_contract import PilotFile, Table

MAX_ROWS = 20_000
MAX_COLUMNS = 128


class NativeRow(NamedTuple):
    number: int
    values: dict[str, object]
    error: str | None = None


def _headers(values: tuple | list) -> tuple[str, ...]:
    names = tuple(v.strip() if isinstance(v, str) else "" for v in values)
    if not 0 < len(names) <= MAX_COLUMNS or any(not n or len(n) > 128 for n in names) or len(set(names)) != len(names):
        raise ValueError("invalid header")
    return names


def _csv(raw: bytes) -> tuple[tuple[str, ...], list[NativeRow]]:
    text = raw.decode("utf-8-sig")
    if "\x00" in text:
        raise ValueError("unexpected null")
    records = csv.reader(StringIO(text, newline=""), strict=True)
    header = _headers(next(records, []))
    rows = []
    for number, cells in enumerate(records, start=2):
        if number > MAX_ROWS + 1:
            raise ValueError("row limit")
        if not cells:
            continue
        if len(cells) != len(header):
            rows.append(NativeRow(number, {}, "column_count"))
        else:
            rows.append(NativeRow(number, dict(zip(header, cells, strict=True))))
    return header, rows


def _xlsx(raw: bytes, sheet: str) -> tuple[tuple[str, ...], list[NativeRow]]:
    with ZipFile(BytesIO(raw)) as archive:
        files = archive.infolist()
        if (len(files) > 256 or sum(f.file_size for f in files) > 48 * 1024 * 1024
            or any(f.flag_bits & 1 or "vbaProject" in f.filename for f in files)):
            raise ValueError("unsafe workbook")
    workbook = load_workbook(BytesIO(raw), read_only=True, data_only=False, keep_links=False)
    try:
        if sheet not in workbook.sheetnames:
            raise KeyError("unknown sheet")
        selected = workbook[sheet]
        if selected.max_column is None or selected.max_row is None:
            raise ValueError("missing worksheet dimensions")
        if selected.max_column > MAX_COLUMNS or selected.max_row > MAX_ROWS + 1:
            raise ValueError("worksheet too large")
        values = selected.iter_rows(max_col=selected.max_column)
        header_row = next(values, ())
        if any(cell.data_type == "f" for cell in header_row):
            raise ValueError("formula in header")
        header = _headers(tuple(cell.value for cell in header_row))
        rows = []
        for number, record in enumerate(values, start=2):
            if number > MAX_ROWS + 1:
                raise ValueError("row limit")
            if any(cell.data_type in {"f", "e"} for cell in record):
                rows.append(NativeRow(number, {}, "formula_or_error_cell"))
            elif any(cell.value is not None for cell in record):
                rows.append(NativeRow(number, dict(zip(header, (cell.value for cell in record), strict=True))))
        return header, rows
    finally:
        workbook.close()


def _geojson(raw: bytes) -> tuple[tuple[str, ...], list[NativeRow]]:
    data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields)
    if not isinstance(data, dict) or data.get("type") != "FeatureCollection" or "crs" in data:
        raise ValueError("expected a WGS84 FeatureCollection")
    features = data.get("features")
    if not isinstance(features, list) or len(features) > MAX_ROWS:
        raise ValueError("feature count")
    rows = []
    headers = {"geometry"}
    for number, feature in enumerate(features, start=1):
        if (not isinstance(feature, dict) or feature.get("type") != "Feature"
            or not isinstance(feature.get("properties"), dict) or "geometry" in feature["properties"]):
            rows.append(NativeRow(number, {}, "invalid_feature"))
            continue
        fields = feature["properties"]
        if len(fields) >= MAX_COLUMNS:
            raise ValueError("too many feature properties")
        headers.update(fields)
        rows.append(NativeRow(number, {**fields, "geometry": feature.get("geometry")}))
    return _headers(tuple(sorted(headers))), rows


def read_native(table: Table, source: PilotFile, raw: bytes) -> tuple[tuple[str, ...], list[NativeRow]]:
    try:
        if source.filename.endswith(".csv"):
            return _csv(raw)
        if source.filename.endswith(".xlsx") and source.sheet is not None:
            return _xlsx(raw, source.sheet)
        if table == "gis":
            return _geojson(raw)
    except (BadZipFile, InvalidFileException, csv.Error, KeyError, OSError, ParseError, RecursionError, TypeError, ValueError):
        raise IntakeError("invalid_source_format", table=table) from None
    raise IntakeError("unsupported_table_format", table=table)
