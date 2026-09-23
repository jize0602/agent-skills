"""Network-free tests for the local continuity initializer and validator."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "context.py"


def load_context_module():
    spec = importlib.util.spec_from_file_location("continuity_context", SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


context = load_context_module()


class ContextTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="continuity-context-")
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_init_generates_contract_brain_and_validates(self):
        contract = context.init_context(self.root, "owner/repo", "demo")

        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["repository"], "owner/repo")
        self.assertEqual(contract["branch"], "main")
        self.assertTrue(contract["auto_sync"])
        self.assertEqual(contract["test_commands"], [])
        self.assertEqual(
            set(contract["files"]),
            {
                "entry",
                "project",
                "requirements",
                "implementation",
                "history",
                "decisions",
                "incidents",
                "lessons",
                "tasks",
                "tests",
                "deployment",
                "safety",
                "handoff",
                "project_skill",
                "changelog",
            },
        )
        self.assertTrue(context.validate_context(self.root))

        entry = (self.root / contract["files"]["entry"]).read_text(encoding="utf-8")
        self.assertLess(entry.index("history"), entry.index("incidents"))
        self.assertLess(entry.index("incidents"), entry.index("lessons"))
        self.assertLess(entry.index("lessons"), entry.index("changelog"))
        skill = (self.root / contract["files"]["project_skill"]).read_text(encoding="utf-8")
        self.assertEqual(contract["files"]["project_skill"], ".ai/skills/demo-project/SKILL.md")
        self.assertTrue(skill.startswith("---\n"))
        self.assertIn("name: demo-project", skill)
        self.assertIn("description:", skill)

        project_state = json.loads(
            (self.root / contract["files"]["project"]).read_text(encoding="utf-8")
        )
        deployment_state = json.loads(
            (self.root / contract["files"]["deployment"]).read_text(encoding="utf-8")
        )
        self.assertEqual(project_state["status"], "UNKNOWN")
        self.assertEqual(project_state["tests"]["status"], "UNAVAILABLE")
        self.assertEqual(deployment_state["status"], "UNKNOWN")
        self.assertNotIn('"status": "PASS"', json.dumps(project_state))
        self.assertIn("Status: UNAVAILABLE", (self.root / contract["files"]["tests"]).read_text())
        agents_text = (self.root / "AGENTS.md").read_text()
        self.assertIn("continuity-completion-rule", agents_text)
        self.assertIn("commit and push", agents_text)
        self.assertIn("CI", agents_text)
        self.assertIn("remote commit/tree", agents_text)

    def test_cli_init_and_validate_are_local_only(self):
        init = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "init",
                "--root",
                str(self.root),
                "--repository",
                "owner/repo",
                "--name",
                "cli-demo",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(init.returncode, 0, init.stderr)
        validate = subprocess.run(
            [sys.executable, str(SCRIPT), "validate", "--root", str(self.root)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(validate.returncode, 0, validate.stderr)
        self.assertIn("valid", validate.stdout)

    def test_mapping_reuses_existing_equivalents_and_preserves_content(self):
        requirements = self.root / "docs" / "requirements.md"
        implementation = self.root / "docs" / "implementation.md"
        shared = self.root / "docs" / "shared-record.md"
        requirements.parent.mkdir()
        requirements.write_text(
            "Existing requirements.\n"
            "Implementation tracking pointer: `docs/implementation.md`\n",
            encoding="utf-8",
        )
        implementation.write_text(
            "Existing implementation.\n"
            "Requirements tracking pointer: `docs/requirements.md`\n",
            encoding="utf-8",
        )
        shared.write_text("Existing shared history and decisions.\n", encoding="utf-8")
        agents = self.root / "AGENTS.md"
        agents.write_text("# Existing instructions\nKeep this text.\n", encoding="utf-8")
        mapping = self.root / "mapping.json"
        mapping.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "files": {
                        "requirements": "docs/requirements.md",
                        "implementation": "docs/implementation.md",
                        "history": "docs/shared-record.md",
                        "decisions": "docs/shared-record.md",
                    },
                }
            ),
            encoding="utf-8",
        )
        original_requirements = requirements.read_bytes()
        original_implementation = implementation.read_bytes()

        contract = context.init_context(self.root, "owner/repo", "mapped", mapping)
        self.assertEqual(contract["files"]["requirements"], "docs/requirements.md")
        self.assertFalse((self.root / ".ai" / "REQUIREMENTS.md").exists())
        self.assertFalse((self.root / ".ai" / "IMPLEMENTATION.md").exists())
        self.assertFalse((self.root / ".ai" / "HISTORY.md").exists())
        self.assertFalse((self.root / ".ai" / "DECISIONS.md").exists())
        self.assertEqual(requirements.read_bytes(), original_requirements)
        self.assertEqual(implementation.read_bytes(), original_implementation)
        agents_after_first = agents.read_text(encoding="utf-8")

        context.init_context(self.root, "owner/repo", "mapped", mapping)
        self.assertEqual(requirements.read_bytes(), original_requirements)
        self.assertEqual(implementation.read_bytes(), original_implementation)
        self.assertEqual(agents.read_text(encoding="utf-8"), agents_after_first)
        self.assertEqual(agents_after_first.count(context.AGENTS_RULE_MARKER), 1)
        self.assertTrue(context.validate_context(self.root))

    def test_duplicate_missing_generated_path_is_rejected(self):
        mapping = self.root / "ambiguous.json"
        mapping.write_text(
            json.dumps(
                {
                    "files": {
                        "history": "docs/shared.md",
                        "lessons": "docs/shared.md",
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(context.ContextError) as error:
            context.init_context(self.root, "owner/repo", "demo", mapping)
        self.assertIn("missing and ambiguous", str(error.exception))
        self.assertFalse((self.root / "docs" / "shared.md").exists())

    def test_existing_contract_can_use_non_main_branch_and_disabled_auto_sync(self):
        contract = context.init_context(self.root, "owner/repo", "demo")
        contract["branch"] = "feature/continuity"
        contract["auto_sync"] = False
        (self.root / ".ai" / "CONTINUITY.json").write_text(
            json.dumps(contract), encoding="utf-8"
        )
        self.assertTrue(context.validate_context(self.root))
        self.assertEqual(
            context.init_context(self.root, "owner/repo", "demo")["branch"],
            "feature/continuity",
        )

    def test_existing_markdown_project_and_deployment_equivalents_are_valid(self):
        project = self.root / "docs" / "project.md"
        deployment = self.root / "docs" / "deployment.md"
        project.parent.mkdir()
        project.write_text("Existing project state.\n", encoding="utf-8")
        deployment.write_text("Existing deployment state.\n", encoding="utf-8")
        mapping = self.root / "markdown-mapping.json"
        mapping.write_text(
            json.dumps(
                {
                    "project": "docs/project.md",
                    "deployment": "docs/deployment.md",
                }
            ),
            encoding="utf-8",
        )
        context.init_context(self.root, "owner/repo", "demo", mapping)
        self.assertTrue(context.validate_context(self.root))
        self.assertEqual(project.read_text(encoding="utf-8"), "Existing project state.\n")
        self.assertEqual(deployment.read_text(encoding="utf-8"), "Existing deployment state.\n")

    def test_mapping_can_supply_real_test_commands(self):
        mapping = self.root / "commands-mapping.json"
        mapping.write_text(
            json.dumps(
                {
                    "files": {"history": "docs/history.md"},
                    "test_commands": ["make test"],
                }
            ),
            encoding="utf-8",
        )
        contract = context.init_context(self.root, "owner/repo", "demo", mapping)
        self.assertEqual(contract["test_commands"], ["make test"])
        project_state = json.loads(
            (self.root / contract["files"]["project"]).read_text(encoding="utf-8")
        )
        self.assertEqual(project_state["tests"]["status"], "NOT_RUN")
        self.assertIn("make test", (self.root / contract["files"]["tests"]).read_text())
        self.assertTrue(context.validate_context(self.root))

    def test_rejects_traversal_mapping_and_symlink_escape(self):
        outside = Path(self.tempdir.name).parent / (self.root.name + "-outside")
        outside.mkdir()
        try:
            mapping = self.root / "bad-mapping.json"
            mapping.write_text(json.dumps({"requirements": "../escape.md"}), encoding="utf-8")
            with self.assertRaises(context.ContextError):
                context.init_context(self.root, "owner/repo", "demo", mapping)
            self.assertFalse((outside / "escape.md").exists())

            ai_link = self.root / ".ai"
            ai_link.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(context.ContextError):
                context.init_context(self.root, "owner/repo", "demo")
            self.assertFalse((outside / "CONTINUITY.json").exists())
        finally:
            ai_link = self.root / ".ai"
            if ai_link.is_symlink():
                ai_link.unlink()
            outside.rmdir()

    def test_validator_catches_contract_version_and_tracking_pointer(self):
        contract = context.init_context(self.root, "owner/repo", "demo")
        contract_path = self.root / ".ai" / "CONTINUITY.json"
        broken = dict(contract)
        broken["schema_version"] = 2
        contract_path.write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(context.ContextError) as version_error:
            context.validate_context(self.root)
        self.assertIn("schema_version", str(version_error.exception))

        contract_path.write_text(json.dumps(contract), encoding="utf-8")
        requirements_path = self.root / contract["files"]["requirements"]
        requirements_path.write_text("# Requirements\n", encoding="utf-8")
        errors = context.validation_errors(self.root)
        self.assertTrue(any("implementation tracking pointer" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
