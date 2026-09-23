"""Network-free tests for the GitHub continuity receipt validator."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import sync_check  # noqa: E402


COMMIT = "1" * 40
TREE = "2" * 40
HASH = "a" * 64


def valid_receipt() -> dict:
    return {
        "repository": "example/fixture",
        "expected_commit": COMMIT,
        "remote_commit": COMMIT,
        "expected_tree": TREE,
        "remote_tree": TREE,
        "visibility": "private",
        "ci_runs": [
            {
                "workflow": "continuity",
                "head_sha": COMMIT,
                "status": "completed",
                "conclusion": "success",
                "url": "https://github.example/runs/1",
            }
        ],
        "required_workflows": ["continuity"],
        "readback": [
            {
                "path": ".ai/START_HERE.md",
                "expected_sha256": HASH,
                "remote_sha256": HASH,
            }
        ],
        "required_paths": [".ai/START_HERE.md"],
    }


class ReceiptValidationTests(unittest.TestCase):
    def assert_rejected(self, receipt, text):
        with self.assertRaises(sync_check.ReceiptValidationError) as raised:
            sync_check.validate_receipt(receipt)
        self.assertIn(text, str(raised.exception))

    def test_complete_receipt_passes(self):
        self.assertTrue(sync_check.validate_receipt(valid_receipt()))

    def test_private_receipt_without_public_authorization_remains_valid(self):
        receipt = valid_receipt()
        self.assertNotIn("explicit_public_authorization", receipt)
        self.assertTrue(sync_check.validate_receipt(receipt))

    def test_repository_object_and_ci_name_alias_are_supported(self):
        receipt = valid_receipt()
        receipt["repository"] = {"owner": "example", "repo": "fixture"}
        receipt["ci_runs"][0]["name"] = receipt["ci_runs"][0].pop("workflow")
        self.assertTrue(sync_check.validate_receipt(receipt))

    def test_rejects_failed_ci(self):
        receipt = valid_receipt()
        receipt["ci_runs"][0]["conclusion"] = "failure"
        self.assert_rejected(receipt, "conclusion must be success")

    def test_rejects_skipped_ci(self):
        receipt = valid_receipt()
        receipt["ci_runs"][0]["status"] = "skipped"
        self.assert_rejected(receipt, "status must be completed")

    def test_rejects_missing_required_workflow_run(self):
        receipt = valid_receipt()
        receipt["required_workflows"] = ["continuity", "tests"]
        self.assert_rejected(receipt, "missing CI run for required workflow: tests")

    def test_rejects_ci_run_with_stale_commit(self):
        receipt = valid_receipt()
        receipt["ci_runs"][0]["head_sha"] = "3" * 40
        self.assert_rejected(receipt, "head_sha does not match expected_commit")

    def test_rejects_remote_stale_commit(self):
        receipt = valid_receipt()
        receipt["remote_commit"] = "4" * 40
        self.assert_rejected(receipt, "remote_commit does not match expected_commit")

    def test_rejects_stale_remote_tree_even_when_commit_matches(self):
        receipt = valid_receipt()
        receipt["remote_tree"] = "5" * 40
        self.assert_rejected(receipt, "remote_tree does not match expected_tree")

    def test_accepts_public_repository_with_explicit_authorization(self):
        receipt = valid_receipt()
        receipt["visibility"] = "public"
        receipt["explicit_public_authorization"] = True
        self.assertTrue(sync_check.validate_receipt(receipt))

    def test_rejects_public_repository_without_explicit_authorization(self):
        receipt = valid_receipt()
        receipt["visibility"] = "public"
        self.assert_rejected(
            receipt, "public visibility requires explicit_public_authorization=true"
        )

    def test_rejects_public_repository_with_false_explicit_authorization(self):
        receipt = valid_receipt()
        receipt["visibility"] = "public"
        receipt["explicit_public_authorization"] = False
        self.assert_rejected(
            receipt, "public visibility requires explicit_public_authorization=true"
        )

    def test_rejects_empty_ci_runs(self):
        receipt = valid_receipt()
        receipt["ci_runs"] = []
        self.assert_rejected(receipt, "ci_runs must be a non-empty list")

    def test_rejects_empty_readback(self):
        receipt = valid_receipt()
        receipt["readback"] = []
        self.assert_rejected(receipt, "readback must be a non-empty list")

    def test_rejects_missing_required_readback_path(self):
        receipt = valid_receipt()
        receipt["required_paths"] = [".ai/START_HERE.md", ".ai/HANDOFF.md"]
        self.assert_rejected(receipt, "missing readback path: .ai/HANDOFF.md")

    def test_rejects_readback_hash_mismatch(self):
        receipt = valid_receipt()
        receipt["readback"][0]["remote_sha256"] = "b" * 64
        self.assert_rejected(receipt, "readback hash mismatch")

    def test_rejects_unsafe_readback_path(self):
        receipt = valid_receipt()
        receipt["readback"][0]["path"] = "."
        receipt["required_paths"] = ["."]
        self.assert_rejected(receipt, "safe repository-relative path")

    def test_rejects_offline_or_incomplete_receipt(self):
        receipt = valid_receipt()
        del receipt["remote_commit"]
        del receipt["remote_tree"]
        del receipt["ci_runs"]
        del receipt["readback"]
        self.assert_rejected(receipt, "remote_commit must be a full 40-character hexadecimal SHA")
        with self.assertRaises(sync_check.ReceiptValidationError):
            sync_check.validate_receipt({})

    def test_rejects_short_commit_and_tree_values(self):
        receipt = valid_receipt()
        receipt["expected_commit"] = "short"
        receipt["remote_tree"] = "short"
        self.assert_rejected(receipt, "expected_commit must be a full 40-character hexadecimal SHA")


class ConflictClassificationTests(unittest.TestCase):
    def test_equal_when_local_and_remote_match(self):
        self.assertEqual(sync_check.classify("base", "same", "same", False), "equal")

    def test_up_to_date_when_remote_is_the_observed_base(self):
        self.assertEqual(sync_check.classify("base", "local", "base", False), "up_to_date")

    def test_up_to_date_when_remote_is_already_in_local_history(self):
        self.assertEqual(sync_check.classify("base", "local", "remote", True), "up_to_date")

    def test_remote_changed_requires_reconciliation(self):
        self.assertEqual(
            sync_check.classify("base", "local", "remote", False), "remote_changed"
        )


class CommandLineTests(unittest.TestCase):
    def test_validate_receipt_cli_accepts_inline_json(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = sync_check.main(
                ["validate-receipt", "--receipt", json.dumps(valid_receipt())]
            )
        self.assertEqual(code, 0)
        self.assertIn("receipt_validation=PASS", output.getvalue())

    def test_validate_receipt_cli_accepts_json_file_and_rejects_failure(self):
        with tempfile.TemporaryDirectory(prefix="sync-check-") as directory:
            receipt_path = Path(directory) / "receipt.json"
            receipt_path.write_text(json.dumps(valid_receipt()), encoding="utf-8")
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = sync_check.main(
                    ["validate-receipt", "--receipt", str(receipt_path)]
                )
            self.assertEqual(code, 0)
            self.assertIn("receipt_validation=PASS", output.getvalue())

            failed = valid_receipt()
            failed["ci_runs"][0]["conclusion"] = "failure"
            receipt_path.write_text(json.dumps(failed), encoding="utf-8")
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                code = sync_check.main(
                    ["validate-receipt", "--receipt", str(receipt_path)]
                )
            self.assertEqual(code, 1)
            self.assertIn("receipt_validation=FAIL", error.getvalue())


if __name__ == "__main__":
    unittest.main()
