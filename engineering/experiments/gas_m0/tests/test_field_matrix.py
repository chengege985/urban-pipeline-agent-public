"""Catalogue integrity and semantic counterexamples, without network/DB access."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gas_field_builder", ROOT / "tools/build_gas_field_matrix.py")
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)
SOURCE = ROOT.parents[1] / builder.SOURCE_NAME


class FieldMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "data/gas_catalog_438.json").read_text(encoding="utf-8"))
        cls.fields = cls.catalog["fields"]
        cls.by_row = {f["source_row"]: f for f in cls.fields}

    def test_exact_original_row_and_resource_coverage(self):
        original = builder.read_catalog(SOURCE)
        self.assertEqual([f["source_row"] for f in self.fields], list(range(3, 441)))
        self.assertEqual(len({f["field_key"] for f in self.fields}), 438)
        self.assertEqual(len({f["resource_id"] for f in self.fields}), 50)
        for f, raw in zip(self.fields, original):
            with self.subTest(row=f["source_row"]):
                self.assertEqual(f["source_cells"], raw["cells"])
                self.assertEqual(f["field_name"], raw["cells"]["Q"])
                self.assertEqual(f["field_code"], raw["cells"]["R"])

    def test_every_field_has_full_contract(self):
        required = {"source_file", "source_sheet", "source_row", "source_name_cell", "resource_id", "field_name", "target_field", "unit", "unit_status", "object_id", "time_attribute", "business_use", "requiredness", "current_real_records", "responsible_party", "catalog_provider", "primary_category"}
        for f in self.fields:
            with self.subTest(row=f["source_row"]):
                self.assertTrue(required.issubset(f))
                self.assertIn(f["primary_category"], {"A", "B", "C", "D"})
                self.assertFalse(f["has_verified_real_records"])
                self.assertIn("目录", f["current_real_records"])

    def test_synthetic_a_is_not_a_request_for_real_records(self):
        subset = [f for f in self.fields if f["primary_category"] == "A"]
        self.assertEqual(len(subset), 15)
        for f in subset:
            self.assertIn("合成A无需对方交真实记录", "；".join(f["notes"]))
            self.assertIn("重通院开发团队", f["responsible_party"])
        minimum = json.loads((ROOT / "data/gas_minimum_inputs.json").read_text(encoding="utf-8"))
        self.assertFalse(minimum["catalog_supplies_business_values"])
        self.assertEqual(minimum["data_mode"], "synthetic")

    def test_pressure_diameter_and_inspection_fields_not_shifted(self):
        expected = {
            99: ("管道编号", "inspection.pipe_external_id", None),
            102: ("管道级别", "inspection.pipe_level_raw", None),
            103: ("管道长度", "inspection.length_m", "m"),
            104: ("设计压力", "inspection.design_pressure_mpa", "MPa"),
            105: ("公称直径", "inspection.nominal_diameter_mm", "mm"),
            106: ("管道材质", "inspection.material_raw", None),
            107: ("介质", "inspection.medium_raw", None),
            108: ("投用日期", "inspection.commissioned_date", "YYYY-MM-DD"),
            110: ("定期检验日期", "inspection.inspected_date", "YYYY-MM-DD"),
            113: ("管道状态", "inspection.asset_status_raw", None),
        }
        for row, (name, target, unit) in expected.items():
            for actual_row in [row, row + 82]:
                with self.subTest(row=actual_row):
                    f = self.by_row[actual_row]
                    self.assertEqual((f["field_name"], f["target_field"], f["unit"]), (name, target, unit))

    def test_undeclared_units_remain_unknown(self):
        for row in [154, 159, 160, 170, 171, 172, 173, 177, 206, 208, 210, 211, 212, 215, 216, 295, 296, 303, 304, 305]:
            with self.subTest(row=row):
                self.assertIsNone(self.by_row[row]["unit"])
                self.assertEqual(self.by_row[row]["unit_status"], "not_declared")
        self.assertEqual(self.by_row[200]["unit"], "天/万方")
        self.assertIn("禁止擅自改", "；".join(self.by_row[200]["notes"]))

    def test_operation_end_never_mapped_to_service_restore(self):
        for row in [96, 320]:
            f = self.by_row[row]
            self.assertEqual(f["target_field"], "incident.response_ended_at")
            self.assertIn("不等于", f["time_attribute"])
        for row in [77, 85]:
            self.assertIn("冲突待确认", self.by_row[row]["time_attribute"])

    def test_coordinate_fields_never_infer_topology(self):
        for row in [159, 160, 170, 171, 172, 173, 178, 215, 216]:
            f = self.by_row[row]
            self.assertNotIn("node_id", f["target_field"])
            self.assertIn("不能依据距离或画线推断", "；".join(f["notes"]))

    def test_invalid_source_identity_is_preserved_not_adopted(self):
        material = self.by_row[205]
        self.assertTrue(material["source_primary_key_declared"])
        self.assertEqual(material["target_field"], "pipe.material_raw")
        self.assertEqual(material["object_id"]["target_id_status"], "unverified_cross_table_mapping")
        self.assertIn("不合理主键", "；".join(material["notes"]))

    def test_retired_catalogue_and_personal_fields_not_used(self):
        retired = [f for f in self.fields if f["catalog_status"] == "已注销"]
        self.assertEqual(len(retired), 21)
        self.assertTrue(all(f["primary_category"] == "D" for f in retired))
        for row in [3, 7, 73, 218, 224, 267, 274, 306, 307]:
            with self.subTest(row=row):
                self.assertEqual(self.by_row[row]["primary_category"], "D")
                self.assertIsNone(self.by_row[row]["target_field"])

    def test_equipment_is_not_sensor_reading_or_g15(self):
        g = self.catalog["indicator_source_candidates"]
        self.assertEqual(set(g), {f"G{i:02}" for i in range(1,16)})
        self.assertEqual(g["G05"]["kind"], "device_or_risk_point_clue_only")
        self.assertEqual(g["G15"]["kind"], "not_in_catalog")
        self.assertEqual(g["G15"]["rows"], [])
        self.assertIn("没有浓度", self.by_row[251]["business_use"])

    def test_excel_csv_retains_all_rows_and_columns(self):
        path = ROOT / "data/gas_field_matrix.csv"
        self.assertTrue(path.read_bytes().startswith(b"\xef\xbb\xbf"))
        with path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 438)
        self.assertEqual([int(r["字段行号"]) for r in rows], list(range(3, 441)))
        self.assertTrue(all(r["当前真实记录"].startswith("未确认取得") for r in rows))

    def test_rebuild_is_byte_reproducible_and_source_change_rejected(self):
        with tempfile.TemporaryDirectory(prefix="gas_catalog_test_") as tmp:
            target = Path(tmp) / "rebuilt"
            builder.build(SOURCE, target)
            for name in ["gas_catalog_438.json", "gas_field_matrix.csv", "gas_minimum_inputs.json"]:
                self.assertEqual((ROOT/"data"/name).read_bytes(), (target/name).read_bytes())
            changed = Path(tmp)/"changed.xlsx"
            changed.write_bytes(b"not the reviewed workbook")
            with self.assertRaisesRegex(ValueError, "source hash"):
                builder.read_catalog(changed)

    def test_minimum_input_paths_match_actual_m0_json(self):
        minimum = json.loads((ROOT / "data/gas_minimum_inputs.json").read_text(encoding="utf-8"))
        documents = {kind: json.loads((ROOT / minimum["m0_path_contract"][kind]).read_text(encoding="utf-8")) for kind in ["network", "case", "result"]}
        for item in minimum["inputs"]:
            with self.subTest(field=item["target_field"]):
                self.assertIn(item["contract_scope"], {"current_fixture", "planned_platform", "missing"})
                if item["contract_scope"] != "current_fixture":
                    self.assertEqual(item["m0_json_paths"], [])
                    continue
                self.assertTrue(item["m0_json_paths"])
                for path in item["m0_json_paths"]:
                    steps = path.split(".")
                    values = [documents[steps[0]]]
                    for step in steps[1:]:
                        key = step.removesuffix("[]")
                        next_values = []
                        for value in values:
                            self.assertIsInstance(value, dict, path)
                            self.assertIn(key, value, path)
                            if step.endswith("[]"):
                                self.assertIsInstance(value[key], list, path)
                                self.assertTrue(value[key], path)
                                next_values.extend(value[key])
                            else:
                                next_values.append(value[key])
                        values = next_values

    def test_platform_identity_is_explicitly_planned_not_issued(self):
        minimum = json.loads((ROOT / "data/gas_minimum_inputs.json").read_text(encoding="utf-8"))
        item = next(i for i in minimum["inputs"] if i["entity"] == "platform_identity")
        self.assertEqual(item["contract_scope"], "planned_platform")
        self.assertEqual(item["m0_json_paths"], [])
        self.assertIn("M0不生成", item["requiredness"])
        self.assertIn("不构成来源认证", minimum["m0_path_contract"]["provenance_boundary"])
        for f in self.fields:
            if f["target_field"] is not None:
                self.assertEqual(f["mapping_contract_scope"], "future_adapter_concept_only_not_registered")
                self.assertIn("不是当前已注册或已实现", "；".join(f["notes"]))


if __name__ == "__main__":
    unittest.main()
