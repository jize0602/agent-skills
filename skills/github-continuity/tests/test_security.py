from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.security import main, scan, scan_bytes, scan_history  # noqa: E402


class SecurityScannerTests(unittest.TestCase):
    def test_scan_bytes_returns_only_redacted_path_and_category(self):
        secret = "sk-" + "a" * 24
        findings = scan_bytes("current.txt", ('API_TOKEN="{}"\n'.format(secret)).encode())
        self.assertTrue(findings)
        self.assertTrue(any(item["category"] in {"openai_like_key", "assigned_secret"} for item in findings))
        self.assertTrue(all(set(item) == {"path", "category"} for item in findings))
        self.assertNotIn(secret, repr(findings))

    def test_provider_key_next_to_fixture_comment_is_still_rejected(self):
        secret = "sk-" + "f" * 24
        findings = scan_bytes("fixture.txt", ('API_KEY="{}" # test fixture\n'.format(secret)).encode())
        self.assertTrue(any(item["category"] == "openai_like_key" for item in findings))
        self.assertNotIn(secret, repr(findings))

    def test_explicit_fixture_placeholders_are_allowed(self):
        data = (
            b"API_KEY=" + b"fixture-api-key-not-a-real-key\n"
            + b"TOKEN=" + b"dummy-token-value\n"
            + b"PASSWORD=" + b"synthetic-password-not-real\n"
        )
        self.assertEqual(scan_bytes("fixtures/config.txt", data), [])

    def test_env_example_is_scanned_but_real_env_is_forbidden(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("API_KEY=fixture-api-key-not-a-real-key\n", encoding="utf-8")
            example_secret = "sk-" + "b" * 24
            (root / ".env.example").write_text("API_KEY={}\n".format(example_secret), encoding="utf-8")
            findings = scan(root)
        self.assertTrue(any(item["path"] == ".env" and item["category"] == "forbidden_path" for item in findings))
        self.assertTrue(any(item["path"] == ".env.example" and item["category"] == "openai_like_key" for item in findings))
        self.assertNotIn(example_secret, repr(findings))

    def test_runtime_and_credential_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "logs").mkdir()
            (root / "uploads").mkdir()
            (root / ".ssh").mkdir()
            for path in (
                root / "logs" / "app.log",
                root / "uploads" / "photo.bin",
                root / ".ssh" / "id_rsa",
                root / "credentials.json",
                root / "client.sqlite3",
            ):
                path.write_bytes(b"fixture")
            findings = scan(root)
        paths = {item["path"] for item in findings if item["category"] == "forbidden_path"}
        self.assertTrue({"logs", "logs/app.log", "uploads", "uploads/photo.bin", ".ssh", ".ssh/id_rsa", "credentials.json", "client.sqlite3"} <= paths)

    def test_generic_source_dirs_and_public_certificates_are_not_runtime_findings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "data").mkdir()
            (root / "private").mkdir()
            (root / "keys").mkdir()
            (root / "certs").mkdir()
            (root / "data" / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
            (root / "private" / "notes.py").write_text("VALUE = 2\n", encoding="utf-8")
            (root / "keys" / "constants.py").write_text("VALUE = 3\n", encoding="utf-8")
            (root / "certs" / "public.crt").write_text(
                "-----BEGIN CERTIFICATE-----\npublic\n-----END CERTIFICATE-----\n", encoding="utf-8"
            )
            findings = scan(root)
        self.assertEqual(findings, [])

    def test_symlink_is_reported_without_following_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "root"
            outside = base / "outside.txt"
            root.mkdir()
            secret = "sk-" + "c" * 24
            outside.write_text("API_TOKEN={}\n".format(secret), encoding="utf-8")
            try:
                os.symlink(outside, root / "linked.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable")
            findings = scan(root)
        self.assertIn({"path": "linked.txt", "category": "symlink"}, findings)
        self.assertNotIn(secret, repr(findings))

    def test_nested_zip_is_scanned(self):
        secret = "sk-" + "d" * 24
        inner_buffer = io.BytesIO()
        with zipfile.ZipFile(inner_buffer, "w") as inner:
            inner.writestr("secret.txt", "API_TOKEN={}\n".format(secret))
        outer_buffer = io.BytesIO()
        with zipfile.ZipFile(outer_buffer, "w") as outer:
            outer.writestr("inner.zip", inner_buffer.getvalue())
        findings = scan_bytes("bundle.zip", outer_buffer.getvalue())
        self.assertTrue(any(item["path"].endswith("bundle.zip!/inner.zip!/secret.txt") for item in findings))
        self.assertNotIn(secret, repr(findings))

    def test_unknown_binary_and_archive_block_the_audit(self):
        binary_findings = scan_bytes("image.bin", b"\x89PNG\r\n\x1a\n\x00\x01")
        extension_findings = scan_bytes("image.png", b"not actually an image")
        archive_findings = scan_bytes("backup.tar.gz", b"\x1f\x8bnot-inspected")
        self.assertIn({"path": "image.bin", "category": "blocked_binary"}, binary_findings)
        self.assertIn({"path": "image.png", "category": "blocked_binary"}, extension_findings)
        self.assertIn({"path": "backup.tar.gz", "category": "blocked_archive"}, archive_findings)

        oversized_member = io.BytesIO()
        with zipfile.ZipFile(oversized_member, "w") as archive:
            archive.writestr("large.txt", b"x" * (8 * 1024 * 1024 + 1))
        self.assertIn({"path": "large.zip", "category": "blocked_archive"}, scan_bytes("large.zip", oversized_member.getvalue()))

        many_members = io.BytesIO()
        with zipfile.ZipFile(many_members, "w") as archive:
            for index in range(257):
                archive.writestr("member-{}.txt".format(index), b"")
        self.assertIn({"path": "many.zip", "category": "blocked_archive"}, scan_bytes("many.zip", many_members.getvalue()))

        nested = b"leaf"
        for _ in range(9):
            layer = io.BytesIO()
            with zipfile.ZipFile(layer, "w") as archive:
                archive.writestr("inner.zip", nested)
            nested = layer.getvalue()
        self.assertTrue(any(item["category"] == "blocked_archive" for item in scan_bytes("deep.zip", nested)))

    def test_exact_review_credentials_allow_only_listed_static_asset_signatures(self):
        image = b"\x89PNG\r\n\x1a\n\x00reviewed image bytes"
        font = b"wOF2" + b"\x00" * 24
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(image)
            (root / "assets" / "body.woff2").write_bytes(font)
            reviews = {
                "assets/logo.png": self._review(image),
                "assets/body.woff2": self._review(font),
            }
            self.assertEqual(scan(root, reviewed_static_assets=reviews), [])

    def test_missing_mismatched_or_incomplete_review_stays_blocked(self):
        data = b"\x89PNG\r\n\x1a\n\x00reviewed image bytes"
        valid = self._review(data)
        invalid_reviews = (
            None,
            {"other/logo.png": valid},
            {"assets/logo.png": dict(valid, sha256="0" * 64)},
            {"assets/logo.png": {"sha256": valid["sha256"], "reviewer": "reviewer"}},
            {"assets/logo.png": dict(valid, reviewer="  ")},
            {"assets/logo.png": dict(valid, reason="  ")},
            {"assets/logo.png": dict(valid, sha256="not-a-sha256")},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            for reviews in invalid_reviews:
                with self.subTest(reviews=reviews):
                    findings = scan(root, reviewed_static_assets=reviews)
                    self.assertIn(
                        {"path": "assets/logo.png", "category": "blocked_binary"},
                        findings,
                    )

    def test_review_cannot_approve_unlisted_or_misidentified_binary(self):
        pdf = b"%PDF-1.7\x00fixture"
        fake_png = b"not a PNG\x00fixture"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "document.pdf").write_bytes(pdf)
            (root / "assets" / "fake.png").write_bytes(fake_png)
            reviews = {
                "assets/document.pdf": self._review(pdf),
                "assets/fake.png": self._review(fake_png),
            }
            findings = scan(root, reviewed_static_assets=reviews)
        self.assertIn({"path": "assets/document.pdf", "category": "blocked_binary"}, findings)
        self.assertIn({"path": "assets/fake.png", "category": "blocked_binary"}, findings)

    def test_review_cannot_approve_secret_database_or_runtime_content(self):
        private_key = (
            b"\x89PNG\r\n\x1a\n-----BEGIN " + b"PRIVATE KEY-----\nnot-a-real-key\n"
            + b"-----END PRIVATE KEY-----"
        )
        database = b"SQLite format 3\x00" + b"fixture"
        runtime_image = b"\x89PNG\r\n\x1a\n\x00runtime bytes"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "runtime").mkdir()
            (root / "assets" / "key.png").write_bytes(private_key)
            (root / "assets" / "database.png").write_bytes(database)
            (root / "runtime" / "logo.png").write_bytes(runtime_image)
            reviews = {
                "assets/key.png": self._review(private_key),
                "assets/database.png": self._review(database),
            }
            findings = scan(root, reviewed_static_assets=reviews)
            unsafe_path_findings = scan(
                root,
                reviewed_static_assets={"runtime/logo.png": self._review(runtime_image)},
            )
        self.assertIn({"path": "assets/key.png", "category": "private_key"}, findings)
        self.assertIn({"path": "assets/key.png", "category": "blocked_binary"}, findings)
        self.assertIn({"path": "assets/database.png", "category": "database_content"}, findings)
        self.assertIn({"path": "assets/database.png", "category": "blocked_binary"}, findings)
        self.assertIn({"path": "runtime/logo.png", "category": "forbidden_path"}, findings)
        self.assertIn({"path": "runtime/logo.png", "category": "blocked_binary"}, findings)
        self.assertIn({"path": ".ai/STATIC_ASSET_REVIEWS.json", "category": "blocked_review_manifest"}, unsafe_path_findings)
        self.assertNotIn("not-a-real-key", repr(findings))

    def test_review_does_not_apply_to_archive_members(self):
        secret = "sk-" + "g" * 24
        archive_data = io.BytesIO()
        with zipfile.ZipFile(archive_data, "w") as archive:
            archive.writestr("nested/logo.png", b"\x89PNG\r\n\x1a\n\x00nested image")
            archive.writestr("token.txt", "API_TOKEN={}\n".format(secret))
        data = archive_data.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "reviewed.png").write_bytes(data)
            findings = scan(root, reviewed_static_assets={"assets/reviewed.png": self._review(data)})
        self.assertTrue(any(item["category"] == "openai_like_key" for item in findings))
        self.assertTrue(any(item["category"] == "blocked_binary" for item in findings))
        self.assertNotIn(secret, repr(findings))

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_manifest_review_supports_ordinary_root_history_cli(self):
        data = b"\x89PNG\r\n\x1a\n\x00reviewed image bytes"
        entry = {"path": "assets/logo.png", **self._review(data)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            self._write_review_manifest(root, [entry])
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            self._git(root, "add", "assets/logo.png", ".ai/STATIC_ASSET_REVIEWS.json")
            self._git(root, "commit", "-qm", "add reviewed static image")

            output = io.StringIO()
            with patch("sys.stdout", output):
                result = main(["--root", str(root), "--history"])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "security_audit=PASS_WITH_REVIEW\n")

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_malformed_duplicate_and_unsafe_manifests_block_both_scans(self):
        data = b"\x89PNG\r\n\x1a\n\x00reviewed image bytes"
        entry = {"path": "assets/logo.png", **self._review(data)}
        duplicate_key_json = (
            '[{"path":"assets/logo.png","path":"assets/logo.png",'
            '"sha256":"' + entry["sha256"] + '","reviewer":"Security Reviewer",'
            '"reason":"Verified public static image/font asset"}]'
        )
        invalid_manifests = (
            "{not-json",
            json.dumps([entry, entry]),
            duplicate_key_json,
            json.dumps([dict(entry, path="../assets/logo.png")]),
            json.dumps([dict(entry, path="runtime/logo.png")]),
            json.dumps([{key: value for key, value in entry.items() if key != "reason"}]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            self._git(root, "add", "assets/logo.png")
            self._git(root, "commit", "-qm", "add image fixture")

            for manifest_text in invalid_manifests:
                with self.subTest(manifest=manifest_text[:40]):
                    manifest_path = self._write_review_manifest_text(root, manifest_text)
                    for findings in (scan(root), scan_history(root)):
                        self.assertIn(
                            {
                                "path": ".ai/STATIC_ASSET_REVIEWS.json",
                                "category": "blocked_review_manifest",
                            },
                            findings,
                        )
                    output = io.StringIO()
                    with patch("sys.stdout", output):
                        result = main(["--root", str(root), "--history"])
                    self.assertEqual(result, 2)
                    self.assertIn("security_audit=BLOCKED", output.getvalue())
                    manifest_path.unlink()

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_history_old_hash_stays_blocked_when_current_manifest_changes(self):
        old_data = b"\x89PNG\r\n\x1a\n\x00old image bytes"
        current_data = b"\x89PNG\r\n\x1a\n\x00current image bytes"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "assets"
            assets.mkdir()
            image = assets / "logo.png"
            image.write_bytes(old_data)
            self._write_review_manifest(root, [{"path": "assets/logo.png", **self._review(old_data)}])
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            self._git(root, "add", "assets/logo.png", ".ai/STATIC_ASSET_REVIEWS.json")
            self._git(root, "commit", "-qm", "add image fixture")
            approved_old_history = scan_history(root)
            image.write_bytes(current_data)
            self._write_review_manifest(root, [{"path": "assets/logo.png", **self._review(current_data)}])
            current_findings = scan(root)
            history_findings = scan_history(root)
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = main(["--root", str(root), "--history"])

        self.assertEqual(approved_old_history, [])
        self.assertEqual(current_findings, [])
        self.assertIn(
            {"path": "history/assets/logo.png", "category": "blocked_binary"},
            history_findings,
        )
        self.assertEqual(result, 2)
        self.assertIn("security_audit=BLOCKED", output.getvalue())

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_two_reviewed_generations_of_one_path_pass_ordinary_history_cli(self):
        old_data = b"\x89PNG\r\n\x1a\n\x00old approved image"
        new_data = b"\x89PNG\r\n\x1a\n\x00new approved image"
        old_entry = {"path": "assets/logo.png", **self._review(old_data)}
        new_entry = {"path": "assets/logo.png", **self._review(new_data)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            image = root / "assets" / "logo.png"
            image.write_bytes(old_data)
            self._write_review_manifest(root, [old_entry])
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            self._git(root, "add", "assets/logo.png", ".ai/STATIC_ASSET_REVIEWS.json")
            self._git(root, "commit", "-qm", "first reviewed image")

            image.write_bytes(new_data)
            self._write_review_manifest(root, [old_entry, new_entry])
            self._git(root, "add", "assets/logo.png", ".ai/STATIC_ASSET_REVIEWS.json")
            self._git(root, "commit", "-qm", "second reviewed image")
            self.assertEqual(scan(root), [])
            self.assertEqual(scan_history(root), [])
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = main(["--root", str(root), "--history"])

        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "security_audit=PASS_WITH_REVIEW\n")

    def test_duplicate_path_sha_review_is_blocked_even_when_metadata_differs(self):
        data = b"\x89PNG\r\n\x1a\n\x00reviewed image"
        entry = {"path": "assets/logo.png", **self._review(data)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            for duplicate in (entry, dict(entry, reason="Another review")):
                with self.subTest(duplicate=duplicate["reason"]):
                    self._write_review_manifest(root, [entry, duplicate])
                    self.assertIn(
                        {"path": ".ai/STATIC_ASSET_REVIEWS.json", "category": "blocked_review_manifest"},
                        scan(root),
                    )
                    output = io.StringIO()
                    with patch("sys.stdout", output):
                        result = main(["--root", str(root)])
                    self.assertEqual(result, 2)
                    self.assertIn("security_audit=BLOCKED", output.getvalue())

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_manifest_cli_conflict_and_secret_content_cannot_pass(self):
        secret = "sk-" + "h" * 24
        data = b"\x89PNG\r\n\x1a\n\x00API_" + b"TO" + b"KEN=" + secret.encode("ascii")
        entry = {"path": "assets/logo.png", **self._review(data)}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            self._write_review_manifest(root, [entry])
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            self._git(root, "add", "assets/logo.png", ".ai/STATIC_ASSET_REVIEWS.json")
            self._git(root, "commit", "-qm", "add reviewed image fixture")

            output = io.StringIO()
            with patch("sys.stdout", output):
                result = main(["--root", str(root), "--history"])
            self.assertNotEqual(result, 0)
            self.assertNotIn(secret, output.getvalue())

            conflict_output = io.StringIO()
            with patch("sys.stdout", conflict_output):
                conflict_result = main(
                    [
                        "--root",
                        str(root),
                        "--history",
                        "--reviewed-static-asset",
                        "assets/logo.png",
                        entry["sha256"],
                        entry["reviewer"],
                        "Conflicting review reason",
                    ]
                )
        self.assertEqual(result, 2)
        self.assertIn("security_audit=BLOCKED", output.getvalue())
        self.assertEqual(conflict_result, 2)
        self.assertIn("blocked_review_manifest", conflict_output.getvalue())
        self.assertNotIn(secret, conflict_output.getvalue())

    def test_cli_accepts_attributed_review_without_printing_credential_fields(self):
        data = b"\x89PNG\r\n\x1a\n\x00reviewed image bytes"
        review = self._review(data)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.png").write_bytes(data)
            output = io.StringIO()
            with patch("sys.stdout", output):
                result = main(
                    [
                        "--root",
                        str(root),
                        "--reviewed-static-asset",
                        "assets/logo.png",
                        review["sha256"],
                        review["reviewer"],
                        review["reason"],
                    ]
                )
            duplicate_output = io.StringIO()
            with patch("sys.stdout", duplicate_output):
                duplicate_result = main(
                    [
                        "--root",
                        str(root),
                        "--reviewed-static-asset",
                        "assets/logo.png",
                        review["sha256"],
                        review["reviewer"],
                        review["reason"],
                        "--reviewed-static-asset",
                        "assets/logo.png",
                        review["sha256"],
                        review["reviewer"],
                        review["reason"],
                    ]
                )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue(), "security_audit=PASS_WITH_REVIEW\n")
        self.assertNotIn(review["reviewer"], output.getvalue())
        self.assertNotIn(review["reason"], output.getvalue())
        self.assertEqual(duplicate_result, 2)
        self.assertIn("security_audit=BLOCKED\n", duplicate_output.getvalue())

    def test_untracked_pycache_is_ignored_but_tracked_pyc_blocks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            generated = root / "__pycache__" / "module.cpython-39.pyc"
            generated.parent.mkdir()
            generated.write_bytes(b"\x00\x01not-text")
            self.assertEqual(scan(root), [])

            self._git(root, "init", "-q")
            self._git(root, "add", "-f", "__pycache__/module.cpython-39.pyc")
            findings = scan(root)
        self.assertIn(
            {"path": "__pycache__/module.cpython-39.pyc", "category": "blocked_binary"},
            findings,
        )

    @unittest.skipUnless(shutil.which("git"), "git is required for history audit")
    def test_history_finds_secret_in_deleted_reachable_blob_without_printing_value(self):
        secret = "sk-" + "e" * 24
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init", "-q")
            self._git(root, "config", "user.email", "security-test@example.invalid")
            self._git(root, "config", "user.name", "Security Test")
            deleted = root / "deleted-secret.txt"
            deleted.write_text("API_TOKEN={}\n".format(secret), encoding="utf-8")
            self._git(root, "add", "deleted-secret.txt")
            self._git(root, "commit", "-qm", "add fixture")
            deleted.unlink()
            self._git(root, "commit", "-qam", "remove fixture")

            findings = scan_history(root)
            output = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "security.py"), "--root", str(root), "--history"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
        self.assertTrue(any(item["path"].endswith("history/deleted-secret.txt") for item in findings))
        self.assertNotIn(secret, repr(findings))
        self.assertEqual(output.returncode, 1)
        self.assertIn("security_audit=REJECTED", output.stdout)
        self.assertIn("deleted-secret.txt", output.stdout)
        self.assertNotIn(secret, output.stdout + output.stderr)

    @staticmethod
    def _git(root: Path, *arguments: str) -> None:
        subprocess.run(
            ["git", "-C", str(root)] + list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

    @staticmethod
    def _review(data: bytes) -> dict[str, str]:
        return {
            "sha256": hashlib.sha256(data).hexdigest(),
            "reviewer": "Security Reviewer",
            "reason": "Verified public static image/font asset",
        }

    @staticmethod
    def _write_review_manifest(root: Path, entries: list[dict[str, str]]) -> Path:
        return SecurityScannerTests._write_review_manifest_text(root, json.dumps(entries))

    @staticmethod
    def _write_review_manifest_text(root: Path, content: str) -> Path:
        review_dir = root / ".ai"
        review_dir.mkdir(exist_ok=True)
        manifest = review_dir / "STATIC_ASSET_REVIEWS.json"
        manifest.write_text(content, encoding="utf-8")
        return manifest


if __name__ == "__main__":
    unittest.main()
