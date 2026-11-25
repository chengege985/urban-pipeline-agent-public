"""Regression cases for the two independently reviewed GAS-M0 matrix P2 issues."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gas_matrix_fix_builder", ROOT / "tools/build_gas_field_matrix.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SOURCE = ROOT.parents[1] / builder.SOURCE_NAME
ARTIFACTS = ("gas_catalog_438.json", "gas_minimum_inputs.json", "gas_field_matrix.csv")


def files_under(directory):
    return {p.relative_to(directory).as_posix(): p.read_bytes()
            for p in directory.rglob("*") if p.is_file()}


class FieldMatrixFixTests(unittest.TestCase):
    def test_station_pressure_range_does_not_invent_design_semantics(self):
        catalog = json.loads((ROOT / "data/gas_catalog_438.json").read_text(encoding="utf-8"))
        fields = {f["source_row"]: f for f in catalog["fields"]}
        for row, name, target in [(201, "进站压力范围", "station.inlet_pressure_range_raw_mpa"),
                                   (202, "出站压力范围", "station.outlet_pressure_range_raw_mpa")]:
            with self.subTest(row=row):
                record = fields[row]
                self.assertEqual(record["source_cells"]["Q"], name)
                self.assertEqual(record["field_name"], name)
                self.assertEqual(record["target_field"], target)
                self.assertEqual(record["unit"], "MPa")
                self.assertNotIn("design", target)
                self.assertIn("未确认", record["business_use"])
                self.assertIn("不擅加", "；".join(record["notes"]))
                self.assertIn("需业务确认", record["secondary_uses"])
                self.assertFalse(record["has_verified_real_records"])
        self.assertEqual(fields[199]["target_field"], "station.design_pressure_mpa")
        self.assertEqual(fields[200]["target_field"], "station.design_throughput_raw")
        self.assertEqual(catalog["indicator_source_candidates"]["G06"]["kind"], "pressure_attribute_clue_only")

    def test_other_436_field_records_remain_at_reviewed_baseline(self):
        catalog = json.loads((ROOT / "data/gas_catalog_438.json").read_text(encoding="utf-8"))
        untouched = [f for f in catalog["fields"] if f["source_row"] not in {201, 202}]
        self.assertEqual(len(untouched), 436)
        canonical = json.dumps(untouched, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        # Independent review baseline 88af2b3, excluding only the two authorized semantic corrections.
        self.assertEqual(hashlib.sha256(canonical).hexdigest(),
                         "094d3a8ca8705e32834fbac6e0563e62d6abf6a213df4acf4ed9f10c9359d909")

    def test_existing_empty_directory_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-empty-") as directory:
            out = Path(directory) / "existing"; out.mkdir()
            with self.assertRaises(FileExistsError):
                builder.build(SOURCE, out)
            self.assertEqual(list(out.iterdir()), [])

    def test_existing_artifacts_are_not_overwritten_or_partially_written(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-existing-") as directory:
            for index, name in enumerate(ARTIFACTS):
                with self.subTest(existing=name):
                    out = Path(directory) / str(index); out.mkdir()
                    (out / name).write_bytes(b"existing artifact, keep exact bytes\r\n")
                    before = files_under(out)
                    with self.assertRaises(FileExistsError):
                        builder.build(SOURCE, out)
                    self.assertEqual(files_under(out), before)

    def test_output_path_file_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-file-") as directory:
            out = Path(directory) / "existing"; out.write_bytes(b"not a directory")
            with self.assertRaises(FileExistsError):
                builder.build(SOURCE, out)
            self.assertEqual(out.read_bytes(), b"not a directory")

    def test_repeated_build_preserves_all_first_output_bytes(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-repeat-") as directory:
            out = Path(directory) / "new"
            builder.build(SOURCE, out)
            first = files_under(out)
            self.assertEqual(set(first), set(ARTIFACTS))
            for name in ARTIFACTS:
                self.assertEqual(first[name], (ROOT / "data" / name).read_bytes())
            self.assertIn(b"\r\n", first["gas_catalog_438.json"])
            self.assertTrue(first["gas_field_matrix.csv"].startswith(b"\xef\xbb\xbf"))
            with self.assertRaises(FileExistsError):
                builder.build(SOURCE, out)
            self.assertEqual(files_under(out), first)

    def test_output_symlink_does_not_modify_its_target(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-link-") as directory:
            base = Path(directory); target = base / "existing"; target.mkdir()
            (target / "sentinel").write_bytes(b"preserve")
            link = base / "output-link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest(f"test platform cannot create a directory symlink: {error}")
            before = files_under(target)
            with self.assertRaises(FileExistsError):
                builder.build(SOURCE, link)
            self.assertEqual(files_under(target), before)
            self.assertTrue(link.is_symlink())

    def test_invalid_source_creates_no_output_directory(self):
        with tempfile.TemporaryDirectory(prefix="gas-matrix-source-") as directory:
            base = Path(directory); source = base / "changed.xlsx"; source.write_bytes(b"changed source")
            out = base / "must-not-exist"
            with self.assertRaisesRegex(ValueError, "source hash"):
                builder.build(source, out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
