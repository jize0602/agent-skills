"""Network-free tests for verified, non-overwriting Skill installation."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


INSTALLER = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(INSTALLER))
import install  # noqa: E402


REF = "refs/tags/v1.0.0"


def make_repo(root: Path) -> None:
    for skill in install.SKILL_NAMES:
        skill_root = root / "skills" / skill
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text("---\nname: {}\n---\n".format(skill), encoding="utf-8")
        (skill_root / "tool.py").write_text("VALUE = {!r}\n".format(skill), encoding="utf-8")


def make_manifest(root: Path) -> dict:
    skills = []
    for name in install.SKILL_NAMES:
        files = []
        for path in sorted((root / "skills" / name).rglob("*")):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                files.append(
                    {
                        "path": relative,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
        skills.append({"name": name, "version": "1.0.0", "files": files})
    return {
        "schema_version": 1,
        "repository": "jize0602/agent-skills",
        "ref": REF,
        "skills": skills,
    }


def write_manifest(path: Path, manifest: dict) -> None:
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class InstallTests(unittest.TestCase):
    def prepare(self, temporary):
        root = Path(temporary) / "repo"
        root.mkdir()
        make_repo(root)
        manifest_path = Path(temporary) / "manifest.json"
        write_manifest(manifest_path, make_manifest(root))
        return root, manifest_path

    def test_installs_both_skills_to_clean_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            dest = Path(temporary) / "installed"

            installed = install.install_bundle(root, manifest_path, dest, REF)

            self.assertEqual([path.name for path in installed], list(install.SKILL_NAMES))
            for skill in install.SKILL_NAMES:
                self.assertEqual(
                    (dest / skill / "SKILL.md").read_bytes(),
                    (root / "skills" / skill / "SKILL.md").read_bytes(),
                )
                self.assertEqual(
                    (dest / skill / "tool.py").read_bytes(),
                    (root / "skills" / skill / "tool.py").read_bytes(),
                )

    def test_rejects_tampered_file_before_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            (root / "skills" / "github-continuity" / "tool.py").write_text("tampered\n", encoding="utf-8")
            dest = Path(temporary) / "installed"

            with self.assertRaisesRegex(install.InstallError, "sha256 mismatch"):
                install.install_bundle(root, manifest_path, dest, REF)
            self.assertFalse(dest.exists())

    def test_rejects_origin_mismatch_before_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["repository"] = "someone-else/agent-skills"
            write_manifest(manifest_path, manifest)
            dest = Path(temporary) / "installed"

            with self.assertRaisesRegex(install.InstallError, "repository declaration must be"):
                install.install_bundle(root, manifest_path, dest, REF)
            self.assertFalse(dest.exists())

    def test_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"][1]["files"][0]["path"] = (
                "skills/github-continuity/../../outside.txt"
            )
            write_manifest(manifest_path, manifest)

            with self.assertRaisesRegex(install.InstallError, "safe path"):
                install.install_bundle(root, manifest_path, Path(temporary) / "installed", REF)

    def test_refuses_existing_skill_directory_without_overwriting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            dest = Path(temporary) / "installed"
            existing = dest / "github-bootstrap"
            existing.mkdir(parents=True)
            sentinel = existing / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(install.InstallError, "already exists"):
                install.install_bundle(root, manifest_path, dest, REF)

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            self.assertFalse((dest / "github-continuity").exists())

    def test_rejects_unlisted_extra_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            (root / "skills" / "github-bootstrap" / "unexpected.txt").write_text(
                "extra", encoding="utf-8"
            )

            with self.assertRaisesRegex(install.InstallError, "file set mismatch"):
                install.install_bundle(root, manifest_path, Path(temporary) / "installed", REF)

    def test_rejects_version_that_does_not_match_release_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["skills"][0]["version"] = "9.9.9"
            write_manifest(manifest_path, manifest)

            with self.assertRaisesRegex(install.InstallError, "version must match"):
                install.install_bundle(root, manifest_path, Path(temporary) / "installed", REF)

    def test_rejects_symlink_and_does_not_install(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)
            link = root / "skills" / "github-bootstrap" / "alias"
            try:
                link.symlink_to("tool.py")
            except OSError as exc:
                self.skipTest("symlink creation unavailable: {}".format(exc))
            dest = Path(temporary) / "installed"

            with self.assertRaisesRegex(install.InstallError, "symlink"):
                install.install_bundle(root, manifest_path, dest, REF)
            self.assertFalse(dest.exists())

    def test_rejects_unexpected_but_valid_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root, manifest_path = self.prepare(temporary)

            with self.assertRaisesRegex(install.InstallError, "does not match --expected-ref"):
                install.install_bundle(
                    root,
                    manifest_path,
                    Path(temporary) / "installed",
                    "refs/tags/v1.0.1",
                )

    def test_cli_help_states_external_provenance_precondition(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                install.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        help_text = " ".join(output.getvalue().split())
        self.assertIn("PROVENANCE PRECONDITION", help_text)
        self.assertIn("not a fork", help_text)
        self.assertIn("do not prove origin", help_text)
        self.assertIn("--expected-ref", help_text)


if __name__ == "__main__":
    unittest.main()
