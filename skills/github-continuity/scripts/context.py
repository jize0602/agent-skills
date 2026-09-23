"""Initialize and validate a small, file-based project continuity context.

The module deliberately has no GitHub, subprocess, or network integration.  It
only creates local continuity files and validates the local contract that maps
logical continuity records to repository-relative paths.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from collections.abc import Mapping
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional, Union


SCHEMA_VERSION = 1
BRANCH = "main"
DEFAULT_TEST_COMMANDS = ()
CONTRACT_PATH = ".ai/CONTINUITY.json"
AGENTS_PATH = "AGENTS.md"
AGENTS_RULE_MARKER = "<!-- github-continuity-completion-rule -->"

FILE_KEYS = (
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
)


class ContextError(ValueError):
    """Raised when a continuity context cannot be safely created or checked."""


def _safe_relative_path(value: Any, label: str = "path") -> str:
    """Return a safe repository-relative POSIX path or raise ``ContextError``."""

    if not isinstance(value, str) or not value or value.strip() != value:
        raise ContextError(f"{label} must be a nonempty relative path")
    if "\x00" in value:
        raise ContextError(f"{label} contains a NUL byte")
    if os.path.isabs(value):
        raise ContextError(f"{label} must be relative: {value!r}")
    if PurePosixPath(value).is_absolute():
        raise ContextError(f"{label} must be relative: {value!r}")
    windows_path = PureWindowsPath(value)
    if windows_path.drive or windows_path.root:
        raise ContextError(f"{label} must be relative: {value!r}")
    if "\\" in value:
        raise ContextError(f"{label} must use POSIX separators: {value!r}")

    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ContextError(f"{label} contains an unsafe path segment: {value!r}")
    return value


def _safe_project_name(name: Any) -> str:
    if not isinstance(name, str) or not name or name.strip() != name:
        raise ContextError("name must be nonempty and must not have surrounding whitespace")
    if name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ContextError("name must be a single safe path segment")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*", name):
        raise ContextError("name contains unsupported characters")
    return name


def _safe_repository(repository: Any) -> str:
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        raise ContextError("repository must have the form owner/repo")
    return repository


def _root_path(root: Union[str, os.PathLike]) -> Path:
    try:
        resolved = Path(root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContextError(f"root cannot be resolved: {root!r}") from exc
    if not resolved.is_dir():
        raise ContextError(f"root is not a directory: {root!r}")
    return resolved


def _candidate(root: Path, relative: str, label: str) -> Path:
    """Resolve a candidate and prove that symlink resolution stays in root."""

    relative = _safe_relative_path(relative, label)
    candidate = root.joinpath(*relative.split("/"))
    try:
        resolved = candidate.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise ContextError(f"{label} cannot be resolved: {relative}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ContextError(f"{label} escapes root through a symlink: {relative}") from exc
    return candidate


def _check_parent_chain(root: Path, candidate: Path, label: str) -> None:
    try:
        parent_parts = candidate.parent.relative_to(root).parts
    except ValueError as exc:
        raise ContextError(f"{label} parent escapes root") from exc

    current = root
    for part in parent_parts:
        current = current / part
        if not os.path.lexists(current):
            continue
        if current.is_symlink():
            raise ContextError(f"{label} uses a symlinked directory: {current}")
        if not current.is_dir():
            raise ContextError(f"{label} parent is not a directory: {current}")


def _prepare_target(root: Path, relative: str, label: str) -> Path:
    candidate = _candidate(root, relative, label)
    _check_parent_chain(root, candidate, label)
    if os.path.lexists(candidate):
        if candidate.is_symlink():
            raise ContextError(f"{label} is a symlink; refusing to write through it")
        if not candidate.is_file():
            raise ContextError(f"{label} is not a regular file: {relative}")
    return candidate


def _ensure_parent_dirs(root: Path, candidate: Path, label: str) -> None:
    try:
        parent_parts = candidate.parent.relative_to(root).parts
    except ValueError as exc:
        raise ContextError(f"{label} parent escapes root") from exc

    current = root
    for part in parent_parts:
        current = current / part
        if os.path.lexists(current):
            if current.is_symlink() or not current.is_dir():
                raise ContextError(f"{label} parent is unsafe: {current}")
            continue
        try:
            current.mkdir()
        except OSError as exc:
            raise ContextError(f"cannot create parent directory for {label}: {current}") from exc


def _write_if_missing(root: Path, relative: str, content: str, label: str) -> bool:
    candidate = _prepare_target(root, relative, label)
    if os.path.lexists(candidate):
        return False
    _ensure_parent_dirs(root, candidate, label)

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(candidate), flags, 0o644)
    except FileExistsError:
        return False
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    return True


def _append_agents_rule(root: Path) -> bool:
    candidate = _prepare_target(root, AGENTS_PATH, "AGENTS.md")
    if not os.path.lexists(candidate):
        return _write_if_missing(root, AGENTS_PATH, _agents_rule(), "AGENTS.md")

    current = candidate.read_text(encoding="utf-8")
    if AGENTS_RULE_MARKER in current:
        return False
    _ensure_parent_dirs(root, candidate, "AGENTS.md")
    separator = "" if not current else ("" if current.endswith("\n") else "\n")
    if current and current.endswith("\n"):
        separator += "\n"
    with candidate.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(separator + _agents_rule())
    return True


def _agents_rule() -> str:
    return (
        f"{AGENTS_RULE_MARKER}\n"
        "## Continuity completion\n\n"
        "For work in this repository, completion updates the logical files mapped "
        "by `.ai/CONTINUITY.json`. When `auto_sync` is `true`, the scoped auto-sync "
        "may commit and push only those continuity changes, then must wait for CI and "
        "read back the remote commit/tree before reporting synchronization. This rule "
        "does not authorize deployment, credential handling, or unrelated remote work.\n"
    )


def _default_files(name: str) -> Dict[str, str]:
    return {
        "entry": ".ai/START_HERE.md",
        "project": ".ai/PROJECT_STATE.json",
        "requirements": ".ai/REQUIREMENTS.md",
        "implementation": ".ai/IMPLEMENTATION.md",
        "history": ".ai/HISTORY.md",
        "decisions": ".ai/DECISIONS.md",
        "incidents": ".ai/INCIDENTS.md",
        "lessons": ".ai/LESSONS.md",
        "tasks": ".ai/OPEN_TASKS.md",
        "tests": ".ai/TEST_STATE.md",
        "deployment": ".ai/DEPLOYMENT_STATE.json",
        "safety": ".ai/SAFETY_RULES.md",
        "handoff": ".ai/HANDOFF.md",
        "project_skill": f".ai/skills/{name}-project/SKILL.md",
        "changelog": "CHANGELOG.md",
    }


def _mapping_parts(
    mapping: Optional[Union[str, os.PathLike, Mapping[str, Any]]]
) -> tuple[Dict[str, Any], Dict[str, Any]]:
    if mapping is None:
        return {}, {}
    if isinstance(mapping, Mapping):
        document: Any = dict(mapping)
    else:
        mapping_path = Path(mapping).expanduser()
        if not mapping_path.is_file():
            raise ContextError(f"mapping JSON does not exist: {mapping_path}")
        try:
            document = json.loads(mapping_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ContextError(f"mapping JSON cannot be read: {mapping_path}") from exc

    if not isinstance(document, dict):
        raise ContextError("mapping JSON must be an object")
    version = document.get("schema_version", document.get("version"))
    if version is not None and (isinstance(version, bool) or version != SCHEMA_VERSION):
        raise ContextError(f"mapping schema version must be {SCHEMA_VERSION}")
    if "files" in document:
        files = document["files"]
    elif "mapping" in document:
        files = document["mapping"]
    else:
        files = {
            key: value
            for key, value in document.items()
            if key not in ("schema_version", "version", "test_commands")
        }
    if not isinstance(files, dict) or not files:
        raise ContextError("mapping JSON must contain a nonempty file mapping")
    return document, files


def _load_mapping(mapping: Optional[Union[str, os.PathLike, Mapping[str, Any]]]) -> Dict[str, str]:
    if mapping is None:
        return {}
    _, raw = _mapping_parts(mapping)

    unknown = sorted(set(raw) - set(FILE_KEYS))
    if unknown:
        raise ContextError("mapping contains unknown logical files: " + ", ".join(unknown))

    result: Dict[str, str] = {}
    for key, value in raw.items():
        result[key] = _safe_relative_path(value, f"mapping.files.{key}")
    return result


def _load_test_commands(
    mapping: Optional[Union[str, os.PathLike, Mapping[str, Any]]]
) -> Optional[List[str]]:
    if mapping is None:
        return None
    document, _ = _mapping_parts(mapping)
    commands = document.get("test_commands")
    if commands is None:
        return None
    if not isinstance(commands, list):
        raise ContextError("mapping test_commands must be a list")
    if any(not isinstance(command, str) or not command.strip() for command in commands):
        raise ContextError("mapping test_commands must contain nonempty strings")
    return list(commands)


def _contract_shape_errors(contract: Any) -> List[str]:
    errors: List[str] = []
    if not isinstance(contract, dict):
        return ["contract must be a JSON object"]

    expected_keys = {
        "schema_version",
        "repository",
        "branch",
        "auto_sync",
        "files",
        "test_commands",
    }
    unexpected = sorted(set(contract) - expected_keys)
    missing = sorted(expected_keys - set(contract))
    if missing:
        errors.append("contract is missing: " + ", ".join(missing))
    if unexpected:
        errors.append("contract has unexpected fields: " + ", ".join(unexpected))

    if contract.get("schema_version") != SCHEMA_VERSION or isinstance(
        contract.get("schema_version"), bool
    ):
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    repository = contract.get("repository")
    if not isinstance(repository, str) or not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository
    ):
        errors.append("repository must have the form owner/repo")
    branch = contract.get("branch")
    if not isinstance(branch, str) or not branch or branch.strip() != branch:
        errors.append("branch must be a nonempty string")
    if not isinstance(contract.get("auto_sync"), bool):
        errors.append("auto_sync must be a boolean")

    files = contract.get("files")
    if not isinstance(files, dict):
        errors.append("files must be a nonempty object")
        files = {}
    else:
        missing_files = sorted(set(FILE_KEYS) - set(files))
        extra_files = sorted(set(files) - set(FILE_KEYS))
        if missing_files:
            errors.append("files is missing: " + ", ".join(missing_files))
        if extra_files:
            errors.append("files has unexpected entries: " + ", ".join(extra_files))

    for key in FILE_KEYS:
        if key not in files:
            continue
        try:
            value = _safe_relative_path(files[key], f"files.{key}")
        except ContextError as exc:
            errors.append(str(exc))
            continue
        if value in (CONTRACT_PATH, AGENTS_PATH):
            errors.append(f"files.{key} uses a reserved path: {value}")

    commands = contract.get("test_commands")
    if not isinstance(commands, list):
        errors.append("test_commands must be a list")
    elif any(not isinstance(command, str) or not command.strip() for command in commands):
        errors.append("test_commands must contain nonempty strings")
    return errors


def _new_contract(
    repository: str,
    files: Dict[str, str],
    test_commands: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "branch": BRANCH,
        "auto_sync": True,
        "files": files,
        "test_commands": list(
            DEFAULT_TEST_COMMANDS if test_commands is None else test_commands
        ),
    }


def _load_existing_contract(root: Path) -> Optional[Dict[str, Any]]:
    candidate = _prepare_target(root, CONTRACT_PATH, "CONTINUITY.json")
    if not os.path.lexists(candidate):
        return None
    try:
        contract = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ContextError("existing .ai/CONTINUITY.json is not valid JSON") from exc
    errors = _contract_shape_errors(contract)
    if errors:
        raise ContextError("existing continuity contract is invalid: " + "; ".join(errors))
    return contract


def _relative_link(source: str, target: str) -> str:
    source_parent = posixpath.dirname(source) or "."
    return posixpath.relpath(target, source_parent)


def _link(source: str, target: str, label: str) -> str:
    return f"[{label}]({_relative_link(source, target)})"


def _pointer(source: str, target: str, label: str) -> str:
    return f"{label}: `{target}` ({_link(source, target, 'open')})"


def _starter_contents(name: str, contract: Dict[str, Any]) -> Dict[str, str]:
    files = contract["files"]
    entry = files["entry"]
    requirements = files["requirements"]
    implementation = files["implementation"]
    commands = contract["test_commands"]
    test_status = "NOT_RUN" if commands else "UNAVAILABLE"

    read_order = [
        (AGENTS_PATH, "AGENTS.md"),
        (files["project_skill"], "project skill"),
        (files["project"], "project state"),
        (files["deployment"], "deployment state"),
        (requirements, "requirements"),
        (implementation, "implementation"),
        (files["history"], "history"),
        (files["decisions"], "decisions"),
        (files["incidents"], "incidents"),
        (files["lessons"], "lessons"),
        (files["tasks"], "open tasks"),
        (files["tests"], "test state"),
        (files["safety"], "safety rules"),
        (files["handoff"], "handoff"),
        (files["changelog"], "changelog"),
    ]
    read_lines = "\n".join(
        f"{index}. {_link(entry, path, label)}" for index, (path, label) in enumerate(read_order, 1)
    )

    project_state = {
        "schema_version": SCHEMA_VERSION,
        "project": name,
        "status": "UNKNOWN",
        "repository": contract["repository"],
        "branch": contract["branch"],
        "continuity_contract": CONTRACT_PATH,
        "tracking": {
            "requirements": requirements,
            "implementation": implementation,
        },
        "tests": {
            "status": "NOT_RUN" if commands else "UNAVAILABLE",
            "commands": commands,
        },
        "deployment": {"status": "UNKNOWN"},
        "next_action": "Read the mapped files and verify the repository state with evidence.",
    }
    deployment_state = {
        "schema_version": SCHEMA_VERSION,
        "status": "UNKNOWN",
        "test": "UNKNOWN",
        "production": "UNKNOWN",
        "last_verified": "NOT_RUN",
        "note": "Initialization did not inspect or change a deployment.",
    }

    contents = {
        entry: (
            f"# {name} continuity start here\n\n"
            "The repository-local continuity contract is the authoritative map for "
            "the records below. Do not copy facts into a second state file; update "
            "the mapped source and leave an evidence trail.\n\n"
            f"Contract: `{CONTRACT_PATH}`\n\n"
            "Read in this order:\n"
            f"{read_lines}\n\n"
            "Current evidence state:\n"
            "- repository state: UNKNOWN\n"
            f"- tests: {test_status}\n"
            "- deployment: UNKNOWN\n\n"
            "Initialization performed no remote action. Verify facts before recording "
            "a completed state.\n"
        ),
        files["project"]: json.dumps(project_state, ensure_ascii=False, indent=2) + "\n",
        requirements: (
            "# Requirements\n\n"
            "Status: UNKNOWN\n\n"
            "No requirements audit was run by initialization. Record verified "
            "requirements here without copying implementation facts.\n\n"
            f"{_pointer(requirements, implementation, 'Implementation tracking pointer')}\n"
        ),
        implementation: (
            "# Implementation\n\n"
            "Status: UNKNOWN\n\n"
            "No implementation audit was run by initialization. Record verified "
            "implementation facts here without copying requirement text.\n\n"
            f"{_pointer(implementation, requirements, 'Requirements tracking pointer')}\n"
        ),
        files["history"]: (
            "# History\n\nStatus: UNKNOWN\n\n"
            "No historical record was assessed by initialization. Add dated evidence "
            "when it is verified.\n"
        ),
        files["decisions"]: (
            "# Decisions\n\nStatus: UNKNOWN\n\n"
            "No decision history was assessed by initialization. Record decisions once "
            "they are confirmed.\n"
        ),
        files["incidents"]: (
            "# Incidents\n\nStatus: UNKNOWN\n\n"
            "No incident history was assessed by initialization. Do not infer that an "
            "incident-free period was verified.\n"
        ),
        files["lessons"]: (
            "# Lessons\n\nStatus: UNKNOWN\n\n"
            "No lessons were assessed by initialization. Add only evidence-backed "
            "lessons.\n"
        ),
        files["tasks"]: (
            "# Open tasks\n\nStatus: UNKNOWN\n\n"
            "The following verification tasks are starter work, not completion claims:\n\n"
            "- [ ] Verify requirements and implementation against the repository.\n"
            "- [ ] Run the configured tests and record evidence in test state.\n"
            "- [ ] Verify test and production deployment state separately.\n"
        ),
        files["tests"]: (
            "# Test state\n\n"
            + (
                "Status: NOT_RUN\n\n"
                "Initialization does not run commands. Configured commands:\n\n"
                + "\n".join(f"- `{command}`" for command in commands)
                + "\n"
                if commands
                else "Status: UNAVAILABLE\n\n"
                "No real test command is configured. An agent must supply one before "
                "tests can be run or reported.\n"
            )
        ),
        files["deployment"]: json.dumps(deployment_state, ensure_ascii=False, indent=2) + "\n",
        files["safety"]: (
            "# Safety rules\n\n"
            "Status: UNKNOWN\n\n"
            "- Keep credentials, tokens, cookies, databases, and runtime customer data "
            "out of continuity files.\n"
            "- Keep every mapped path relative to the repository and contained by it.\n"
            "- Do not turn local test evidence into deployment or production authorization.\n"
            "- Initialization and validation perform no remote action.\n"
        ),
        files["handoff"]: (
            "# Handoff\n\nStatus: UNKNOWN\n\n"
            "No handoff state was assessed by initialization. The next safe action is to "
            "read START_HERE and verify the local repository facts.\n"
        ),
        files["project_skill"]: (
            "---\n"
            f"name: {name}-project\n"
            f"description: Repository continuity instructions for {name}.\n"
            "---\n\n"
            f"# {name} project skill\n\n"
            "Use the continuity contract as the only path map. Read the entry file, "
            "project state, requirements, implementation, safety rules, tasks, tests, "
            "deployment state, and handoff before changing project state.\n\n"
            "Keep requirements and implementation linked by tracking pointers rather "
            "than duplicated facts. Record UNKNOWN or NOT_RUN until evidence exists. "
            "At completion, follow the scoped AGENTS continuity rule; this initializer "
            "does not perform remote actions.\n"
        ),
        files["changelog"]: (
            "# Changelog\n\n"
            "## Unreleased\n\n"
            "- Initialized missing continuity records and contract mappings.\n"
            f"- Test state remains {test_status} and deployment state remains UNKNOWN.\n"
            "- No remote action was performed.\n"
        ),
    }
    return contents


def _preflight_files(root: Path, contract: Dict[str, Any]) -> None:
    paths = [CONTRACT_PATH, AGENTS_PATH] + [contract["files"][key] for key in FILE_KEYS]
    counts: Dict[str, int] = {}
    for relative in paths:
        counts[relative] = counts.get(relative, 0) + 1
    for relative in paths:
        candidate = _prepare_target(root, relative, relative)
        if counts[relative] > 1 and not os.path.lexists(candidate):
            raise ContextError(
                f"duplicate generated path is missing and ambiguous: {relative}"
            )


def init_context(
    root: Union[str, os.PathLike],
    repository: str,
    name: str,
    mapping: Optional[Union[str, os.PathLike, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Create missing continuity files and return the local contract.

    Existing files are never replaced.  An existing contract is also treated
    as authoritative; conflicting command-line or mapping input is rejected.
    """

    root_path = _root_path(root)
    repository = _safe_repository(repository)
    name = _safe_project_name(name)
    supplied_mapping = _load_mapping(mapping)
    supplied_test_commands = _load_test_commands(mapping)
    existing_contract = _load_existing_contract(root_path)

    if existing_contract is not None:
        if existing_contract["repository"] != repository:
            raise ContextError(
                "existing continuity contract belongs to "
                f"{existing_contract['repository']}, not {repository}"
            )
        existing_files = existing_contract["files"]
        for key, value in supplied_mapping.items():
            if existing_files.get(key) != value:
                raise ContextError(f"mapping conflicts with existing files.{key}")
        if (
            supplied_test_commands is not None
            and existing_contract["test_commands"] != supplied_test_commands
        ):
            raise ContextError("mapping conflicts with existing test_commands")
        contract = existing_contract
    else:
        files = _default_files(name)
        files.update(supplied_mapping)
        contract = _new_contract(repository, files, supplied_test_commands)

    shape_errors = _contract_shape_errors(contract)
    if shape_errors:
        raise ContextError("continuity contract is invalid: " + "; ".join(shape_errors))
    _preflight_files(root_path, contract)

    contents = _starter_contents(name, contract)
    contract_json = json.dumps(contract, ensure_ascii=False, indent=2) + "\n"
    _write_if_missing(root_path, CONTRACT_PATH, contract_json, "CONTINUITY.json")
    for key in FILE_KEYS:
        relative = contract["files"][key]
        _write_if_missing(root_path, relative, contents[relative], relative)
    _append_agents_rule(root_path)
    return contract


def _read_existing_file(root: Path, relative: str, label: str, errors: List[str]) -> Optional[Path]:
    try:
        candidate = _candidate(root, relative, label)
    except ContextError as exc:
        errors.append(str(exc))
        return None
    if not os.path.lexists(candidate):
        errors.append(f"{label} does not exist: {relative}")
        return None
    if not candidate.is_file():
        errors.append(f"{label} is not a regular file: {relative}")
        return None
    return candidate


def _pointer_aliases(source: str, target: str) -> List[str]:
    aliases = [target, _relative_link(source, target)]
    return list(dict.fromkeys(aliases))


def _has_pointer(text: str, source: str, target: str) -> bool:
    return any(alias in text for alias in _pointer_aliases(source, target))


def _has_valid_frontmatter(text: str) -> bool:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        closing = next(index for index in range(1, len(lines)) if lines[index].strip() == "---")
    except StopIteration:
        return False
    fields: Dict[str, str] = {}
    for line in lines[1:closing]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return bool(fields.get("name")) and bool(fields.get("description"))


def validation_errors(root: Union[str, os.PathLike]) -> List[str]:
    """Return all local contract validation errors without changing anything."""

    try:
        root_path = _root_path(root)
    except ContextError as exc:
        return [str(exc)]

    errors: List[str] = []
    contract_file = _read_existing_file(root_path, CONTRACT_PATH, "CONTINUITY.json", errors)
    if contract_file is None:
        return errors
    try:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"CONTINUITY.json is not valid JSON: {exc}"]

    errors.extend(_contract_shape_errors(contract))
    if errors:
        return errors

    texts: Dict[str, str] = {}
    for key in FILE_KEYS:
        relative = contract["files"][key]
        candidate = _read_existing_file(root_path, relative, f"files.{key}", errors)
        if candidate is not None:
            try:
                text = candidate.read_text(encoding="utf-8")
                if not text.strip():
                    errors.append(f"files.{key} is empty: {relative}")
                else:
                    texts[key] = text
            except (OSError, UnicodeError) as exc:
                errors.append(f"files.{key} cannot be read: {exc}")

    if errors:
        return errors

    if not _has_valid_frontmatter(texts["project_skill"]):
        errors.append("project_skill must begin with valid name/description frontmatter")

    try:
        requirements_text = texts["requirements"]
        implementation_text = texts["implementation"]
        requirements_path = contract["files"]["requirements"]
        implementation_path = contract["files"]["implementation"]
        if requirements_path != implementation_path:
            if not _has_pointer(requirements_text, requirements_path, implementation_path):
                errors.append("requirements file lacks an implementation tracking pointer")
            if not _has_pointer(implementation_text, implementation_path, requirements_path):
                errors.append("implementation file lacks a requirements tracking pointer")
    except KeyError as exc:
        errors.append(f"required continuity file was not readable: {exc}")
    return errors


def validate_context(root: Union[str, os.PathLike]) -> bool:
    """Validate the local contract, returning ``True`` or raising ``ContextError``."""

    errors = validation_errors(root)
    if errors:
        raise ContextError("continuity validation failed:\n- " + "\n- ".join(errors))
    return True


# Small aliases make the module convenient for callers while keeping the CLI
# names and the public function names obvious.
initialize_context = init_context
validate = validate_context


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Initialize or validate local continuity files")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="create missing continuity files")
    init_parser.add_argument("--root", required=True, help="repository root")
    init_parser.add_argument("--repository", required=True, help="owner/repo")
    init_parser.add_argument("--name", required=True, help="project name")
    init_parser.add_argument("--mapping", help="JSON file containing logical file mappings")

    validate_parser = subparsers.add_parser("validate", help="validate local continuity files")
    validate_parser.add_argument("--root", required=True, help="repository root")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error("a command is required: init or validate")

    try:
        if args.command == "init":
            contract = init_context(args.root, args.repository, args.name, args.mapping)
            print(
                f"Initialized continuity context for {contract['repository']} "
                f"at {Path(args.root).expanduser().resolve()}"
            )
        else:
            validate_context(args.root)
            print("Continuity context is valid")
    except ContextError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
