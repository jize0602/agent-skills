#!/usr/bin/env python3
"""Validate an offline GitHub continuity receipt.

This module deliberately only parses and validates supplied data.  It does not
call GitHub, inspect a checkout, or write a receipt or any other file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional, Sequence


FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
FULL_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReceiptValidationError(ValueError):
    """Raised when a continuity receipt cannot prove a successful sync."""

    def __init__(self, errors: Sequence[str]):
        self.errors = tuple(errors)
        super().__init__("; ".join(self.errors))


def _full_sha(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA.fullmatch(value) is not None


def _full_sha256(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA256.fullmatch(value.lower()) is not None


def _non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _safe_repository_path(value: Any) -> bool:
    """Accept a repository-relative POSIX path and reject path traversal."""

    if not _non_empty_string(value) or "\\" in value or value.startswith("/"):
        return False
    path = PurePosixPath(value)
    if value in {".", ".."} or path.as_posix() != value:
        return False
    return not any(part in {"", ".", "..", ".git"} for part in path.parts)


def _string_list(value: Any, field: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or not value:
        errors.append(f"{field} must be a non-empty list")
        return []
    if any(not _non_empty_string(item) for item in value):
        errors.append(f"{field} must contain only non-empty strings")
        return []
    result = [item for item in value if isinstance(item, str)]
    if len(set(result)) != len(result):
        errors.append(f"{field} must not contain duplicates")
    return result


def _repository_label(receipt: Mapping[str, Any]) -> Optional[str]:
    repository = receipt.get("repository")
    if isinstance(repository, str):
        parts = repository.split("/")
        if len(parts) == 2 and all(_non_empty_string(part) for part in parts):
            return repository
        return None
    if isinstance(repository, Mapping):
        owner = repository.get("owner")
        repo = repository.get("repo")
        if _non_empty_string(owner) and _non_empty_string(repo):
            return f"{owner}/{repo}"
    return None


def _require_sha(
    receipt: Mapping[str, Any], field: str, errors: list[str]
) -> Optional[str]:
    value = receipt.get(field)
    if not _full_sha(value):
        errors.append(f"{field} must be a full 40-character hexadecimal SHA")
        return None
    return value


def validate_receipt(receipt: Mapping[str, Any]) -> bool:
    """Validate a complete, already-decoded continuity receipt.

    ``True`` is returned for a valid receipt.  Invalid or incomplete evidence
    raises :class:`ReceiptValidationError`; callers can report its redacted
    validation messages without printing receipt contents.
    """

    if not isinstance(receipt, Mapping):
        raise ReceiptValidationError(["receipt must be a JSON object"])

    errors: list[str] = []
    repository = _repository_label(receipt)
    if repository is None:
        errors.append("repository must identify an owner/repo")

    expected_commit = _require_sha(receipt, "expected_commit", errors)
    remote_commit = _require_sha(receipt, "remote_commit", errors)
    expected_tree = _require_sha(receipt, "expected_tree", errors)
    remote_tree = _require_sha(receipt, "remote_tree", errors)

    if expected_commit and remote_commit and expected_commit != remote_commit:
        errors.append("remote_commit does not match expected_commit")
    if expected_tree and remote_tree and expected_tree != remote_tree:
        errors.append("remote_tree does not match expected_tree")

    visibility = receipt.get("visibility")
    if visibility == "public":
        if receipt.get("explicit_public_authorization") is not True:
            errors.append(
                "public visibility requires explicit_public_authorization=true"
            )
    elif visibility != "private":
        errors.append("visibility must be private or explicitly authorized public")

    required_workflows = _string_list(
        receipt.get("required_workflows"), "required_workflows", errors
    )

    ci_runs = receipt.get("ci_runs")
    seen_workflows: set[str] = set()
    if not isinstance(ci_runs, list) or not ci_runs:
        errors.append("ci_runs must be a non-empty list")
        ci_runs = []
    for index, run in enumerate(ci_runs):
        prefix = f"ci_runs[{index}]"
        if not isinstance(run, Mapping):
            errors.append(f"{prefix} must be an object")
            continue

        workflow = run.get("workflow")
        alternate_workflow = run.get("name")
        if workflow is None:
            workflow = alternate_workflow
        elif alternate_workflow is not None and workflow != alternate_workflow:
            errors.append(f"{prefix} workflow and name differ")
        if not _non_empty_string(workflow):
            errors.append(f"{prefix}.workflow must be a non-empty string")
        else:
            seen_workflows.add(workflow)

        head_sha = run.get("head_sha")
        if not _full_sha(head_sha):
            errors.append(f"{prefix}.head_sha must be a full 40-character hexadecimal SHA")
        elif expected_commit and head_sha != expected_commit:
            errors.append(f"{prefix}.head_sha does not match expected_commit")

        if run.get("status") != "completed":
            errors.append(f"{prefix}.status must be completed")
        if run.get("conclusion") != "success":
            errors.append(f"{prefix}.conclusion must be success")
        if not _non_empty_string(run.get("url")):
            errors.append(f"{prefix}.url must be a non-empty string")

    for workflow in required_workflows:
        if workflow not in seen_workflows:
            errors.append(f"missing CI run for required workflow: {workflow}")

    required_paths = _string_list(
        receipt.get("required_paths"), "required_paths", errors
    )

    readback = receipt.get("readback")
    readback_by_path: dict[str, Mapping[str, Any]] = {}
    if not isinstance(readback, list) or not readback:
        errors.append("readback must be a non-empty list")
        readback = []
    for index, item in enumerate(readback):
        prefix = f"readback[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        path = item.get("path")
        if not _safe_repository_path(path):
            errors.append(f"{prefix}.path must be a safe repository-relative path")
            continue
        if path in readback_by_path:
            errors.append(f"duplicate readback path: {path}")
            continue
        readback_by_path[path] = item

        expected_sha256 = item.get("expected_sha256")
        remote_sha256 = item.get("remote_sha256")
        if not _full_sha256(expected_sha256):
            errors.append(f"{prefix}.expected_sha256 must be a 64-character hexadecimal SHA-256")
        if not _full_sha256(remote_sha256):
            errors.append(f"{prefix}.remote_sha256 must be a 64-character hexadecimal SHA-256")
        if (
            _full_sha256(expected_sha256)
            and _full_sha256(remote_sha256)
            and expected_sha256.lower() != remote_sha256.lower()
        ):
            errors.append(f"readback hash mismatch: {path}")

    for path in required_paths:
        if path not in readback_by_path:
            errors.append(f"missing readback path: {path}")

    if errors:
        raise ReceiptValidationError(errors)
    return True


def classify(
    base: str, local: str, remote: str, remote_is_ancestor: bool
) -> str:
    """Classify whether a local sync can proceed without reconciliation.

    ``base`` is the remote commit observed before local work.  A remote equal
    to that base, or already contained in the local history, is safe to call
    ``up_to_date``.  A different remote that is not an ancestor requires
    reconciliation; this helper never chooses a merge or overwrite.
    """

    if not all(isinstance(value, str) and value for value in (base, local, remote)):
        raise ValueError("base, local, and remote must be non-empty strings")
    if not isinstance(remote_is_ancestor, bool):
        raise TypeError("remote_is_ancestor must be a bool")
    if local == remote:
        return "equal"
    if remote == base or remote_is_ancestor:
        return "up_to_date"
    return "remote_changed"


def _load_receipt(value: str) -> Mapping[str, Any]:
    """Load inline JSON or a JSON file without creating or modifying files."""

    candidate = value.strip()
    if candidate.startswith(("{", "[")):
        decoded = json.loads(candidate)
    else:
        decoded = json.loads(Path(candidate).read_text(encoding="utf-8"))
    if not isinstance(decoded, Mapping):
        raise ReceiptValidationError(["receipt must be a JSON object"])
    return decoded


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate offline continuity evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser(
        "validate-receipt", help="validate a JSON receipt from an external readback"
    )
    validate.add_argument(
        "--receipt", required=True, help="receipt JSON text or path to a JSON file"
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        receipt = _load_receipt(args.receipt)
        validate_receipt(receipt)
    except (OSError, json.JSONDecodeError, ReceiptValidationError, UnicodeError) as exc:
        print(f"receipt_validation=FAIL: {exc}", file=sys.stderr)
        return 1

    repository = _repository_label(receipt) or "unknown"
    print(f"receipt_validation=PASS repository={repository}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
