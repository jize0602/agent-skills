from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
BACKUP_PATH = SCRIPTS / "backup.py"
SPEC = importlib.util.spec_from_file_location("continuity_backup", BACKUP_PATH)
backup = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(backup)


def run_git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repository,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout.strip()


class BackupTest(unittest.TestCase):
    def make_repository(self) -> tuple[tempfile.TemporaryDirectory, Path, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="continuity-backup-test-")
        base = Path(temporary.name)
        repository = base / "fixture-repository"
        repository.mkdir()
        run_git(repository, "init", "--quiet")
        run_git(repository, "config", "user.email", "fixture@example.invalid")
        run_git(repository, "config", "user.name", "Fixture")
        with mock.patch.object(backup.context, "DEFAULT_TEST_COMMANDS", ("fixture-test",)):
            backup.context.init_context(repository, "fixture/repository", "Fixture")
        (repository / "README.md").write_text("fixture\n", encoding="utf-8")
        (repository / "nested").mkdir()
        (repository / "nested" / "value.txt").write_text("payload\n", encoding="utf-8")
        (repository / ".env.example").write_text(
            "API_KEY=fixture-api-key-not-a-real-key\n", encoding="utf-8"
        )
        executable = repository / "run-fixture.sh"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        run_git(repository, "add", ".")
        run_git(repository, "commit", "--quiet", "-m", "fixture")
        return temporary, repository, base

    def test_pack_verify_tamper_and_restore_real_git_fixture(self):
        temporary, repository, base = self.make_repository()
        self.addCleanup(temporary.cleanup)
        archive = base / "continuity-backup.zip"

        scanner = mock.Mock()
        scanner.scan.return_value = []
        with mock.patch.object(backup, "security", scanner):
            created = backup.pack(
                repository,
                archive,
                created_at="2026-09-22T12:34:56Z",
            )
        self.assertEqual(created, archive)
        scanner.scan.assert_called_once_with(repository.resolve())

        with zipfile.ZipFile(archive) as opened:
            names = opened.namelist()
            self.assertEqual(names[:2], ["BACKUP_METADATA.json", "MANIFEST.json"])
            self.assertIn("RESTORE.md", names)
            self.assertIn(b"GitHub Release Asset", opened.read("RESTORE.md"))
            self.assertIn(".env.example", names)
            self.assertIn("run-fixture.sh", names)
            metadata = json.loads(opened.read("BACKUP_METADATA.json"))
            manifest = json.loads(opened.read("MANIFEST.json"))
        self.assertEqual(metadata["schema_version"], 1)
        self.assertEqual(metadata["kind"], "ordinary-continuity-backup")
        self.assertEqual(metadata["created_at"], "2026-09-22T12:34:56Z")
        self.assertFalse(metadata["protected"])
        self.assertFalse(metadata["production"])
        self.assertFalse(metadata["formal"])
        self.assertFalse(metadata["milestone"])
        self.assertEqual(
            [entry["path"] for entry in manifest["files"] if entry["path"] in {"README.md", "nested/value.txt"}],
            ["README.md", "nested/value.txt"],
        )
        self.assertEqual(backup.verify(archive)["verified"], True)

        tampered = base / "tampered.zip"
        with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(tampered, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "nested/value.txt":
                    data = b"tampered\n"
                target.writestr(info, data)
        with self.assertRaises(backup.BackupError):
            backup.verify(tampered)

        destination = base / "restored"
        self.assertEqual(backup.restore(archive, destination), destination)
        self.assertEqual((destination / "README.md").read_text(encoding="utf-8"), "fixture\n")
        self.assertEqual(
            (destination / "nested" / "value.txt").read_text(encoding="utf-8"),
            "payload\n",
        )
        self.assertTrue(os.access(destination / "run-fixture.sh", os.X_OK))
        with self.assertRaises(backup.BackupError):
            backup.restore(archive, destination)

        protected = base / "protected.zip"
        with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(protected, "w") as target:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == "BACKUP_METADATA.json":
                    protected_metadata = json.loads(data)
                    protected_metadata["protected"] = True
                    data = backup._json_bytes(protected_metadata)
                target.writestr(info, data)
        self.assertTrue(backup.verify(protected)["metadata"]["protected"])
        protected_destination = base / "protected-restored"
        backup.restore(protected, protected_destination)
        self.assertEqual((protected_destination / "README.md").read_text(encoding="utf-8"), "fixture\n")

    def test_pack_rejects_dirty_repository_and_runtime_files(self):
        temporary, repository, base = self.make_repository()
        self.addCleanup(temporary.cleanup)
        with mock.patch.object(backup, "security", None):
            with self.assertRaises(backup.BackupError):
                backup.pack(repository, base / "missing-security.zip")

        (repository / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")
        with self.assertRaises(backup.BackupError):
            with mock.patch.object(backup, "security", mock.Mock(scan=mock.Mock(return_value=[]))):
                backup.pack(repository, base / "dirty.zip")

        (repository / "uncommitted.txt").unlink()
        (repository / ".env.local").write_text("SECRET=fixture\n", encoding="utf-8")
        run_git(repository, "add", ".env.local")
        run_git(repository, "commit", "--quiet", "-m", "runtime secret")
        with self.assertRaises(backup.BackupError):
            with mock.patch.object(backup, "security", mock.Mock(scan=mock.Mock(return_value=[]))):
                backup.pack(repository, base / "secret.zip")

    def test_pack_source_receipt_uses_remote_commit_and_rejects_tree_mismatch(self):
        temporary, repository, base = self.make_repository()
        self.addCleanup(temporary.cleanup)
        local_commit = run_git(repository, "rev-parse", "HEAD")
        local_tree = run_git(repository, "rev-parse", "HEAD^{tree}")
        readback_path = repository / ".ai" / "START_HERE.md"
        readback_hash = hashlib.sha256(readback_path.read_bytes()).hexdigest()
        remote_commit = "f" * 40
        receipt = {
            "repository": "fixture/repository",
            "expected_commit": remote_commit,
            "remote_commit": remote_commit,
            "expected_tree": local_tree,
            "remote_tree": local_tree,
            "visibility": "private",
            "required_workflows": ["continuity"],
            "ci_runs": [
                {
                    "workflow": "continuity",
                    "head_sha": remote_commit,
                    "status": "completed",
                    "conclusion": "success",
                    "url": "https://example.invalid/ci/1",
                }
            ],
            "required_paths": [".ai/START_HERE.md"],
            "readback": [
                {
                    "path": ".ai/START_HERE.md",
                    "expected_sha256": readback_hash,
                    "remote_sha256": readback_hash,
                }
            ],
        }
        receipt_path = base / "source-receipt.json"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        scanner = mock.Mock()
        scanner.scan.return_value = []
        archive = base / "remote-source.zip"
        with mock.patch.object(backup, "security", scanner):
            backup.pack(repository, archive, source_receipt=receipt_path)
        with zipfile.ZipFile(archive) as opened:
            metadata = json.loads(opened.read("BACKUP_METADATA.json"))
        self.assertEqual(metadata["commit"], remote_commit)
        self.assertEqual(metadata["tree"], local_tree)
        self.assertEqual(metadata["provenance_local_commit"], local_commit)

        mismatched = dict(receipt)
        mismatched["expected_tree"] = "0" * 40
        mismatched["remote_tree"] = "0" * 40
        mismatched_path = base / "mismatched-receipt.json"
        mismatched_path.write_text(json.dumps(mismatched), encoding="utf-8")
        with mock.patch.object(backup, "security", scanner):
            with self.assertRaises(backup.BackupError):
                backup.pack(repository, base / "mismatched-source.zip", source_receipt=mismatched_path)

    def test_retention_plan_keeps_newest_three_and_never_selects_protected_records(self):
        def metadata(timestamp: str, **flags):
            values = {
                "schema_version": 1,
                "kind": "ordinary-continuity-backup",
                "repository": "fixture-repository",
                "commit": "a" * 40,
                "tree": "b" * 40,
                "created_at": timestamp + "Z",
                "protected": False,
                "production": False,
                "formal": False,
                "milestone": False,
            }
            values.update(flags)
            return values

        def record(identifier: int, timestamp: str, **kwargs):
            date, time = timestamp.split("T", 1)
            tag = "continuity-backup-" + date.replace("-", "") + "-" + time.replace(":", "")
            values = {
                "id": identifier,
                "tag_name": tag,
                "repository": "fixture-repository",
                "verified": True,
                "metadata": metadata(timestamp, **kwargs.pop("metadata_flags", {})),
            }
            values.update(kwargs)
            return values

        inventory = {
            "repository": "fixture-repository",
            "backups": [
                record(101, "2026-09-18T12:00:00"),
                record(102, "2026-09-19T12:00:00"),
                record(103, "2026-09-20T12:00:00"),
                record(104, "2026-09-21T12:00:00"),
                record(105, "2026-09-22T12:00:00", metadata_flags={"formal": True}),
                record(106, "2026-09-23T12:00:00", protected=True),
                record(107, "2026-09-24T12:00:00", verified=False),
                {
                    "id": 108,
                    "tag_name": "continuity-backup-20260925-120000",
                    "repository": "other-repository",
                    "verified": True,
                    "metadata": metadata("2026-09-25T12:00:00"),
                },
                {
                    "id": 109,
                    "tag_name": "not-a-continuity-backup",
                    "verified": True,
                    "metadata": metadata("2026-09-26T12:00:00"),
                },
            ],
        }
        self.assertEqual(
            backup.retention_plan(inventory),
            [101],
        )

        mixed_repository = [record(201, "2026-09-18T12:00:00"), record(202, "2026-09-19T12:00:00")]
        mixed_repository[1]["repository"] = "other-repository"
        self.assertEqual(backup.retention_plan({"backups": mixed_repository}), [])
        missing_repository = [record(301, "2026-09-18T12:00:00")]
        missing_repository[0].pop("repository")
        self.assertEqual(backup.retention_plan({"backups": missing_repository}), [])


if __name__ == "__main__":
    unittest.main()
