import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from offline_git_bundle import export_bundle, receive_bundle, git


class OfflineBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="offline-bundle-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source, self.target = self.root / "source", self.root / "target"
        self.source.mkdir()
        git(self.source, "init", "-b", "main")
        git(self.source, "config", "user.name", "Local test")
        git(self.source, "config", "user.email", "test@example.invalid")
        (self.source / "base.txt").write_text("base")
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "baseline")
        self.base = git(self.source, "rev-parse", "HEAD")
        subprocess.run(["git", "clone", "--no-hardlinks", str(self.source), str(self.target)],
                       check=True, capture_output=True)
        self.branch = "refactor/test-boundaries"
        git(self.source, "switch", "-c", self.branch)
        (self.source / "added.txt").write_text("candidate")
        git(self.source, "add", ".")
        git(self.source, "commit", "-m", "candidate")
        self.bundle = self.root / "change.bundle"
        self.meta = self.bundle.with_suffix(".bundle.json")

    def export(self):
        return export_bundle(self.source, self.base, self.branch, self.bundle)

    def receive(self):
        return receive_bundle(self.target, self.bundle, self.meta, self.branch)

    def test_shared_history_roundtrip_does_not_merge_or_checkout(self):
        result = self.export()
        self.assertEqual(self.receive()["head_commit"], result["head_commit"])
        self.assertEqual(git(self.target, "branch", "--show-current"), "main")
        self.assertEqual(git(self.target, "rev-parse", "HEAD"), self.base)
        self.assertFalse((self.target / "added.txt").exists())

    def test_existing_transfer_is_preserved(self):
        self.export()
        original = self.bundle.read_bytes()
        with self.assertRaises(ValueError):
            self.export()
        self.assertEqual(self.bundle.read_bytes(), original)

    def test_dirty_source_is_rejected(self):
        (self.source / "uncommitted").write_text("keep")
        with self.assertRaises(ValueError):
            self.export()

    def test_dirty_target_is_preserved(self):
        self.export()
        (self.target / "uncommitted").write_text("keep")
        with self.assertRaises(ValueError):
            self.receive()
        self.assertEqual((self.target / "uncommitted").read_text(), "keep")

    def test_corrupt_bundle_rejected_before_branch_creation(self):
        self.export()
        self.bundle.write_bytes(self.bundle.read_bytes() + b"corruption")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.receive()
        self.assertEqual(git(self.target, "branch", "--list", self.branch), "")

    def test_manifest_wrong_branch_is_rejected(self):
        self.export()
        data = json.loads(self.meta.read_text())
        data["branch"] = "refactor/something-else"
        self.meta.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "branch mismatch"):
            self.receive()

    def test_main_target_is_rejected(self):
        with self.assertRaises(ValueError):
            receive_bundle(self.target, self.bundle, self.meta, "main")

    def test_existing_review_branch_is_not_overwritten(self):
        self.export()
        self.receive()
        before = git(self.target, "rev-parse", self.branch)
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.receive()
        self.assertEqual(git(self.target, "rev-parse", self.branch), before)

    def test_unrelated_target_rejected(self):
        self.export()
        unrelated = self.root / "unrelated"
        unrelated.mkdir()
        git(unrelated, "init", "-b", "main")
        with self.assertRaises(ValueError):
            receive_bundle(unrelated, self.bundle, self.meta, self.branch)

    def test_wrong_diff_manifest_does_not_create_branch(self):
        self.export()
        data = json.loads(self.meta.read_text())
        data["changed_paths"] = []
        self.meta.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "Changed-path"):
            self.receive()
        self.assertEqual(git(self.target, "branch", "--list", self.branch), "")


if __name__ == "__main__":
    unittest.main()
