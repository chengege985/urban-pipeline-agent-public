"""Check the release-local synthetic store against its pinned delivery sources."""

import json
from pathlib import Path
import tempfile
import unittest

from package_gas_demo import add_pilot_profile
from prepare_gas_pilot_demo import DATASET, PROFILE, prepare_pilot
from src.gas_application.pilot_store import PilotStore


class GasPilotDemoRestoreTests(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.release = self.root / "release"
        self.release.mkdir()
        (self.release / "gas-demo-release.json").write_text(json.dumps({
            "commit": "a" * 40, "demo_profile": PROFILE, "pilot_demo": add_pilot_profile(self.release),
        }), encoding="utf-8")
        self.runtime = self.root / "runtime"

    def test_cold_and_warm_start_keep_exact_synthetic_versions_and_one_receipt(self):
        store_path = prepare_pilot(self.release, self.runtime)
        self.assertEqual(store_path, prepare_pilot(self.release, self.runtime))
        store = PilotStore(store_path)
        entries = store.entries(frozenset({DATASET}))
        self.assertEqual([row.version for row, _ in entries], ["demo-v2", "demo-v3"])
        self.assertEqual([row.version for row, _ in entries if row.preferred], ["demo-v3"])
        self.assertTrue(all(row.data_mode == "synthetic" for row, _ in entries))
        receipt, = store.rejected(frozenset({DATASET}))
        self.assertEqual(receipt[0].quality.missing_fields[0].fields, ("observed_at",))
        self.assertFalse((store_path / "approvals.json").exists())

    def test_changed_rejection_source_cannot_be_published_as_an_expected_rejection(self):
        source = self.release / "pilot-synthetic/demo-v3-rejected/observations.csv"
        source.write_text("modified synthetic bytes", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source bytes differ"):
            prepare_pilot(self.release, self.runtime)
        self.assertFalse((self.runtime / ("pilot-" + "a" * 40)).exists())

    def test_changed_manifest_and_quarantined_warm_store_fail_closed(self):
        quote = self.release / "pilot-synthetic/demo-v3/manifest.json"
        original = quote.read_bytes()
        quote.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "manifest digest"):
            prepare_pilot(self.release, self.runtime)
        quote.write_bytes(original)
        store = PilotStore(prepare_pilot(self.release, self.runtime))
        store.change_version(DATASET, "demo-v3", action="quarantine", actor="test", reason="synthetic scope test")
        with self.assertRaisesRegex(ValueError, "store differs"):
            prepare_pilot(self.release, self.runtime)
