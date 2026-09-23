"""Network-free tests for deterministic public Skill manifests."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import release_manifest  # noqa: E402


REF = "refs/tags/v1.0.0"


def make_repo(root: Path) -> None:
    (root / "VERSION.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": "jize0602/agent-skills",
                "skills": [
                    {"name": skill, "version": "1.0.0"}
                    for skill in release_manifest.SKILL_NAMES
                ],
            }
        ),
        encoding="utf-8",
    )
    for skill in release_manifest.SKILL_NAMES:
        skill_root = root / "skills" / skill
        skill_root.mkdir(parents=True)
        (skill_root / "SKILL.md").write_text("---\nname: {}\n---\n".format(skill), encoding="utf-8")
        (skill_root / "tool.py").write_text("VALUE = {!r}\n".format(skill), encoding="utf-8")


class ReleaseManifestTests(unittest.TestCase):
    def test_manifest_is_deterministic_and_contains_release_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)

            first = release_manifest.build_manifest(root, REF)
            second = release_manifest.build_manifest(root, REF)

            self.assertEqual(release_manifest.render_manifest(first), release_manifest.render_manifest(second))
            self.assertEqual(first["schema_version"], 1)
            self.assertEqual(first["repository"], "jize0602/agent-skills")
            self.assertEqual(first["ref"], REF)
            self.assertEqual(
                [skill["name"] for skill in first["skills"]],
                ["github-bootstrap", "github-continuity"],
            )
            for skill in first["skills"]:
                self.assertEqual(skill["version"], "1.0.0")
                for item in skill["files"]:
                    contents = (root / item["path"]).read_bytes()
                    self.assertEqual(item["sha256"], hashlib.sha256(contents).hexdigest())

    def test_rejects_non_tag_ref(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)
            with self.assertRaisesRegex(release_manifest.ManifestError, "specific tag"):
                release_manifest.build_manifest(root, "main")

    def test_rejects_tag_version_that_differs_from_version_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)
            versions = json.loads((root / "VERSION.json").read_text(encoding="utf-8"))
            versions["skills"][0]["version"] = "v1.0.0"
            (root / "VERSION.json").write_text(json.dumps(versions), encoding="utf-8")

            with self.assertRaisesRegex(release_manifest.ManifestError, "without v"):
                release_manifest.build_manifest(root, REF)

    def test_rejects_invalid_semver_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)
            with self.assertRaisesRegex(release_manifest.ManifestError, "semantic version"):
                release_manifest.build_manifest(root, "refs/tags/v1.0")

    def test_rejects_symlink_in_skill_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)
            link = root / "skills" / "github-bootstrap" / "alias"
            try:
                link.symlink_to("tool.py")
            except OSError as exc:
                self.skipTest("symlink creation unavailable: {}".format(exc))
            with self.assertRaisesRegex(release_manifest.ManifestError, "symlink"):
                release_manifest.build_manifest(root, REF)

    def test_manifest_output_cannot_be_inside_skill_tree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            root.mkdir()
            make_repo(root)
            manifest = release_manifest.build_manifest(root, REF)
            output = root / "skills" / "github-bootstrap" / "manifest.json"
            with self.assertRaisesRegex(release_manifest.ManifestError, "self-reference"):
                release_manifest._ensure_output_is_outside_skills(
                    output,
                    release_manifest._skill_roots(root),
                )


if __name__ == "__main__":
    unittest.main()
