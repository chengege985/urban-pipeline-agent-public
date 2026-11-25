"""Check restored frontend assets against a release with the original manifest format."""
import json
import os
import shutil
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from prepare_gas_demo import git, prepare, sha256


class GasDemoRestoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name)
        cache = patch.dict("os.environ", {"WPN_GAS_DEMO_CACHE": str(self.workspace / "cache")})
        cache.start()
        self.addCleanup(cache.stop)

        source = self.workspace / "source"
        source.mkdir()
        git(source, "init", "--quiet")
        (source / ".gitignore").write_text("web/dist/\n")
        (source / "scripts").mkdir()
        scripts = Path(__file__).resolve().parent
        shutil.copyfile(scripts / "gas_dev.py", source / "scripts/gas_dev.py")
        wrappers = self.workspace / "scripts"
        wrappers.mkdir()
        for name in ("gas_demo.sh", "prepare_gas_demo.py"):
            shutil.copyfile(scripts / name, wrappers / name)
        git(source, "add", ".gitignore", "scripts/gas_dev.py")
        git(source, "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
            "commit", "--quiet", "-m", "initial")
        bundle = self.workspace / "source.bundle"
        git(source, "bundle", "create", str(bundle), "--all")

        dist = self.workspace / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text('<script src="assets/app.js"></script>')
        (dist / "assets/app.js").write_text("console.log('demo')")
        self.archive = self.workspace / "web-dist.tar.gz"
        with tarfile.open(self.archive, "w:gz") as out:
            out.add(dist, arcname="dist")
        manifest = {
            "commit": git(source, "rev-parse", "HEAD"),
            "tree": git(source, "rev-parse", "HEAD^{tree}"),
            "bundle_sha256": sha256(bundle),
            "frontend_sha256": sha256(self.archive),
            "index_sha256": sha256(dist / "index.html"),
        }
        (self.workspace / "gas-demo-release.json").write_text(json.dumps(manifest))
        self.checkout = prepare(self.workspace)
        self.asset = self.checkout / "web/dist/assets/app.js"

    def test_cached_script_is_missing(self):
        self.asset.unlink()
        with self.assertRaisesRegex(ValueError, "cached frontend"):
            prepare(self.workspace)

    def test_cached_script_has_same_size_but_different_content(self):
        self.asset.write_bytes(b"?" * self.asset.stat().st_size)
        with self.assertRaisesRegex(ValueError, "cached frontend"):
            prepare(self.workspace)

    def test_unexpected_cached_asset(self):
        self.asset.with_name("old.js").write_text("obsolete")
        with self.assertRaisesRegex(ValueError, "cached frontend"):
            prepare(self.workspace)

    def command(self, action):
        return subprocess.run(
            ["bash", str(self.workspace / "scripts/gas_demo.sh"), action, "--json"],
            text=True, capture_output=True, timeout=10,
            env=os.environ | {"WPN_GAS_DEMO_RUNTIME": str(self.workspace / "runtime"),
                              "WPN_GAS_DEMO_MODEL": "off"},
        )

    def test_management_survives_missing_frontend_and_restart_stops_first(self):
        self.asset.unlink()
        status = self.command("status")
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(json.loads(status.stdout)["frontend"], "stopped")
        stop = self.command("stop")
        self.assertEqual(stop.returncode, 0, stop.stderr)
        self.assertTrue(json.loads(stop.stdout)["stopped"])
        state = self.workspace / "runtime/state.json"
        state.unlink()
        restart = self.command("restart")
        self.assertNotEqual(restart.returncode, 0)
        self.assertIn("cached frontend", restart.stderr)
        self.assertEqual(json.loads(state.read_text())["status"], "stopped")

    def test_management_rejects_changed_manager_source(self):
        manager = self.checkout / "scripts/gas_dev.py"
        manager.write_text("raise RuntimeError('unverified manager must not execute')")
        result = self.command("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cached source differs", result.stderr)
        self.assertFalse((self.workspace / "runtime/state.json").exists())

    def test_legacy_cache_upgrades_from_original_archive(self):
        cache = self.checkout.parent
        next(cache.glob("frontend-*.tar.gz")).unlink()
        self.assertEqual(prepare(self.workspace), self.checkout)
        self.assertTrue(next(cache.glob("frontend-*.tar.gz")).is_file())

    def test_damaged_cached_archive_is_rejected(self):
        cached_archive = next(self.checkout.parent.glob("frontend-*.tar.gz"))
        cached_archive.write_bytes(b"incomplete archive")
        with self.assertRaisesRegex(ValueError, "cached frontend archive"):
            prepare(self.workspace)

    def test_valid_warm_cache_does_not_need_the_delivery_archive(self):
        self.archive.unlink()
        self.assertEqual(prepare(self.workspace), self.checkout)
