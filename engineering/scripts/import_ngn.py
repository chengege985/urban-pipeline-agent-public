"""Build a read-only NGN snapshot from the three original operator files."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, time
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import zipfile
import zlib

from openpyxl import load_workbook
import pyproj
import shapely


SOURCES = {
    "assets": (20, "ODP17", "NGN_live_distribution_mains_The_North_Geo_Package_02e52f007b.zip"),
    "emergency": (5, "ODP4", "Public_Reported_Emergencies_f5ca0eb22b.xlsx"),
    "interruption": (6, "ODP5", "Unplanned_Interruptions_f4dee433e1.xlsx"),
}
LAYER = "NGN_live_distribution_mains_The_North"
FIELDS = ("TYPE", "PRESSURE", "MATERIAL", "DIAMETER", "DIAM_UNIT", "CARR_MAT", "CARR_DIA",
          "CARR_DI_UN", "ASSET_ID", "AG_IND", "INST_DATE", "DEPTH")


def scalar(value):
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, (date, time)):
        return value.isoformat()
    return value


def source_metadata(kind, file):
    page, reference, original_name = SOURCES[kind]
    if file.name != original_name:
        raise ValueError(f"Unexpected source filename for {reference}: {file.name}")
    with file.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"reference": reference, "file": file.name, "url": f"https://northerngasopendataportal.co.uk/dataset/{page}",
            "download_url": f"https://cms.northerngasopendataportal.co.uk/uploads/{file.name}",
            "sha256": digest, "publisher": "Northern Gas Networks",
            "license": "https://creativecommons.org/licenses/by/4.0/"}


def insert_assets(db, archive, scratch):
    with zipfile.ZipFile(archive) as source:
        members = [n for n in source.namelist() if n.lower().endswith(".gpkg")]
        if len(members) != 1:
            raise ValueError("Expected exactly one GeoPackage")
        package = scratch / "source.gpkg"
        with source.open(members[0]) as inp, package.open("wb") as out:
            shutil.copyfileobj(inp, out)
    with sqlite3.connect(package.resolve().as_uri() + "?mode=ro", uri=True) as check:
        meta = check.execute("SELECT column_name,geometry_type_name,srs_id FROM gpkg_geometry_columns WHERE table_name=?", (LAYER,)).fetchone()
        if meta != ("Shape", "MULTILINESTRING", 27700):
            raise ValueError(f"Unexpected NGN geometry contract: {meta}")
        source_count = check.execute(f'SELECT count(*) FROM "{LAYER}"').fetchone()[0]
    transformer = pyproj.Transformer.from_crs("EPSG:27700", "EPSG:4326", always_xy=True)
    stats = Counter()
    cells = Counter()
    source = sqlite3.connect(package.resolve().as_uri() + "?mode=ro", uri=True)
    columns = ",".join(f'"{field}"' for field in FIELDS)
    try:
        cursor = source.execute(f'SELECT OBJECTID, Shape,{columns} FROM "{LAYER}" ORDER BY OBJECTID')
        while records := cursor.fetchmany(8000):
            insert_asset_batch(db, records, transformer, cells, stats)
    finally:
        source.close()
    imported = db.execute("SELECT count(*) FROM assets").fetchone()[0]
    if not imported or imported != source_count:
        raise ValueError(f"Source/import count mismatch: {source_count}/{imported}")
    duplicate = db.execute("SELECT count(*) FROM (SELECT asset_id FROM assets WHERE asset_id IS NOT NULL AND asset_id<>'' GROUP BY asset_id HAVING count(*)>1)").fetchone()[0]
    bbox = db.execute("SELECT min(minx),min(miny),max(maxx),max(maxy) FROM asset_rtree").fetchone()
    return dict(stats, rows=imported, drawable=imported-stats["invalid_geometry"], duplicate_asset_ids=duplicate, bbox=bbox,
                layer=LAYER, source_crs="EPSG:27700", display_crs="EPSG:4326",
                transform=transformer.get_last_used_operation().description,
                proj=pyproj.proj_version_str)


def geopackage_wkb(blob):
    if blob is None:
        return None
    if len(blob) < 8 or blob[:3] != b"GP\x00" or blob[3] & 0xe0:
        raise ValueError("Unsupported GeoPackage geometry header")
    flags = blob[3]
    envelope_sizes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
    envelope = (flags >> 1) & 7
    srs = struct.unpack_from("<i" if flags & 1 else ">i", blob, 4)[0]
    if srs != 27700 or envelope not in envelope_sizes:
        raise ValueError("Unexpected geometry CRS or envelope type")
    return None if flags & 16 else blob[8 + envelope_sizes[envelope]:]


def insert_asset_batch(db, records, transformer, cells, stats):
    geometries = shapely.from_wkb([geopackage_wkb(row[1]) for row in records])
    projected = shapely.transform(geometries, transformer.transform, interleaved=False)
    rows, spatial = [], []
    for (fid, _, *values), shape in zip(records, projected):
        attrs = {key: scalar(value) for key, value in zip(FIELDS, values)}
        unit, diameter = attrs["DIAM_UNIT"], attrs["DIAMETER"]
        mm = round(diameter * (25.4 if unit == "I" else 1), 6) if isinstance(diameter, (int, float)) and diameter > 0 and unit in ("MM", "I") else None
        record = int(fid)
        location = None
        rank = 10**9
        # Spatially stratified overview. The detailed viewport retains original geometry.
        if shape is not None and not shape.is_empty and shape.is_valid:
            x0, y0, x1, y1 = map(float, shape.bounds)
            if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90):
                raise ValueError(f"Invalid transformed coordinates for {record}")
            spatial.append((record, x0, x1, y0, y1))
            location = zlib.compress(shapely.to_geojson(shape).encode())
            cell = (int((x0 + x1) * 20), int((y0 + y1) * 20))
            rank = cells[cell]
            cells[cell] += 1
        else:
            stats["invalid_geometry"] += 1
        stats["invalid_diameter"] += mm is None
        stats["missing_asset_id"] += not bool(attrs["ASSET_ID"])
        rows.append((record, attrs["ASSET_ID"], attrs["MATERIAL"], attrs["PRESSURE"], mm, rank, json.dumps(attrs), location))
    db.executemany("INSERT INTO assets VALUES (?,?,?,?,?,?,?,?)", rows)
    db.executemany("INSERT INTO asset_rtree VALUES (?,?,?,?,?)", spatial)


def insert_history(db, kind, path):
    book = load_workbook(path, read_only=True, data_only=True)
    try:
        rows = book["Data"].values
        expected = {
            "emergency": ("Unique ID", "Maint. Activity Type", "Priority", "Received Date", "Received Time",
                          "Receipt to Arrival (minutes)", "Postcode"),
            "interruption": ("Interruption Start Date", "Interruption Start Time", "REP Field", "Interruption Category",
                             "Duration", "Postcode", "Multi Occupancy?", "Number of Floors"),
        }[kind]
        if tuple(str(x).strip() for x in next(rows)) != expected:
            raise ValueError(f"Unexpected {kind} columns")
        stats = Counter()
        batch = []
        for rownum, row in enumerate(rows, 2):
            if all(v is None for v in row):
                continue
            is_emergency = kind == "emergency"
            day, clock = (row[3], row[4]) if is_emergency else row[:2]
            day = day.date() if isinstance(day, datetime) else day
            day = day.isoformat() if isinstance(day, date) else None
            clock = clock.isoformat() if isinstance(clock, time) else None
            label = row[1] if is_emergency else row[2]
            stats["rows"] += 1
            stats["undated"] += day is None
            batch.append((kind, rownum, str(row[0]) if is_emergency and row[0] is not None else None,
                          day, clock, label, json.dumps({k: scalar(v) for k,v in zip(expected, row)}, ensure_ascii=False)))
            if len(batch) >= 5000:
                db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", batch)
                batch.clear()
        db.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", batch)
        limits = db.execute("SELECT min(day),max(day) FROM events WHERE kind=?", (kind,)).fetchone()
        return dict(stats, coverage="NGN 全网（来源发布范围）", first_date=limits[0], last_date=limits[1])
    finally:
        book.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="A new snapshot directory")
    args = parser.parse_args()
    version_sources = {}
    for kind, (_, _, filename) in SOURCES.items():
        file = args.source_dir / filename
        if file.is_file():
            version_sources[kind] = source_metadata(kind, file)
        elif kind == "assets":
            raise FileNotFoundError(file)
    args.output.mkdir(parents=False, exist_ok=False)
    database = args.output / "ngn_catalog.sqlite3"
    with tempfile.TemporaryDirectory(dir=args.output) as temp, sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE assets(fid INTEGER PRIMARY KEY, asset_id TEXT, material TEXT, pressure TEXT,
                diameter_mm REAL, map_rank INTEGER, attrs TEXT NOT NULL, geom BLOB);
            CREATE VIRTUAL TABLE asset_rtree USING rtree(fid,minx,maxx,miny,maxy);
            CREATE TABLE events(kind TEXT, source_row INTEGER, job_id TEXT, day TEXT, clock TEXT, category TEXT,
                attrs TEXT NOT NULL, PRIMARY KEY(kind,source_row));
        """)
        assets = insert_assets(db, args.source_dir / version_sources["assets"]["file"], Path(temp))
        history = {kind: insert_history(db, kind, args.source_dir / version_sources[kind]["file"])
                   for kind in ("emergency","interruption") if kind in version_sources}
        db.executescript("""
            CREATE INDEX assets_code ON assets(asset_id);
            CREATE INDEX assets_material ON assets(material);
            CREATE INDEX assets_pressure ON assets(pressure);
            CREATE INDEX assets_diameter ON assets(diameter_mm);
            CREATE INDEX assets_map_rank ON assets(map_rank,fid);
            CREATE INDEX events_day ON events(kind,day DESC,source_row DESC);
        """)
    with database.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    manifest = {"dataset_id": "ngn_the_north", "data_version": f"ngn-odp17-20261104-{digest[:12]}",
                "database": database.name, "database_sha256": digest, "sources": version_sources,
                "assets": assets, "history": history}
    (args.output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"assets": assets, "history": history, "version": manifest["data_version"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
