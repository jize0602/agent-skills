#!/usr/bin/env python3
"""Create, verify, restore, and plan retention for continuity backups.

The archive format is intentionally small: two JSON control files, restore
instructions, and the committed regular files from a clean Git repository.  No operation in
this module deletes a backup or overwrites a destination.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple
import zipfile


try:
    from . import security  # type: ignore
except (ImportError, ValueError):
    try:
        import security  # type: ignore
    except ImportError:
        security = None  # type: ignore

try:
    from . import context  # type: ignore
except (ImportError, ValueError):
    try:
        import context  # type: ignore
    except ImportError:
        context = None  # type: ignore

try:
    from . import sync_check  # type: ignore
except (ImportError, ValueError):
    try:
        import sync_check  # type: ignore
    except ImportError:
        sync_check = None  # type: ignore


METADATA_NAME = "BACKUP_METADATA.json"
MANIFEST_NAME = "MANIFEST.json"
RESTORE_NAME = "RESTORE.md"
RESTORE_INSTRUCTIONS = (
    "# Restore this continuity snapshot\n\n"
    "1. Confirm this ZIP came from the intended GitHub Release Asset and compare its "
    "SHA-256 with the published checksum.\n"
    "2. Run the installed github-continuity backup verifier, then restore into a "
    "new empty directory. Do not overwrite a checkout or a newer remote commit.\n"
    "3. Read BACKUP_METADATA.json for the source repository and commit. Read "
    ".ai/START_HERE.md, the mapped safety rules, requirements, tests and handoff.\n"
    "4. Run the project tests. Reconnect Git history from GitHub separately; this "
    "source snapshot does not contain .git or runtime customer data.\n"
).encode("utf-8")
SCHEMA_VERSION = 1
BACKUP_KIND = "ordinary-continuity-backup"
RETENTION_KEEP = 3

MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MEMBER_COUNT = 10000
MAX_COMPRESSION_RATIO = 1000
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_HASH_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_TAG_RE = re.compile(r"^continuity-backup-(\d{8})-(\d{6})$")

_FORBIDDEN_DIRS = {"log", "logs", "runtime", "session", "sessions", "upload", "uploads"}
_FORBIDDEN_NAMES = {
    "credentials",
    "credentials.json",
    "secret",
    "secret.json",
    "secrets",
    "secrets.json",
    "token",
    "tokens",
}
_FORBIDDEN_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pid",
    ".pfx",
    ".sqlite",
    ".sqlite-shm",
    ".sqlite-wal",
    ".sqlite3",
)


class BackupError(ValueError):
    """Raised when a backup cannot be safely created or used."""


def _git(root: Path, *args: str, optional: bool = False) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise BackupError("git is required for repository backups") from exc
    if completed.returncode != 0:
        if optional:
            return b""
        raise BackupError("git command failed while inspecting the repository")
    return completed.stdout


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _repository_root(root: Path) -> Path:
    candidate = Path(root).expanduser()
    if not candidate.is_dir():
        raise BackupError("repository root must be an existing directory")
    candidate = candidate.resolve()
    raw_top = _git(candidate, "rev-parse", "--show-toplevel").decode("utf-8", "strict").strip()
    actual_top = Path(raw_top).resolve()
    if actual_top != candidate:
        raise BackupError("--root must be the Git repository root")
    return candidate


def _require_clean(root: Path) -> None:
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise BackupError("repository must have a clean committed HEAD")


def _commit_and_tree(root: Path) -> Tuple[str, str]:
    commit = _git(root, "rev-parse", "HEAD").decode("ascii", "strict").strip()
    tree = _git(root, "rev-parse", "HEAD^{tree}").decode("ascii", "strict").strip()
    if not _SHA_RE.fullmatch(commit) or not _SHA_RE.fullmatch(tree):
        raise BackupError("repository HEAD has an invalid commit or tree SHA")
    return commit, tree


def _remote_repository(root: Path) -> Optional[str]:
    remote = _git(root, "config", "--get", "remote.origin.url", optional=True)
    value = remote.decode("utf-8", "replace").strip()
    if not value:
        return None
    value = value.rstrip("/")
    if "://" in value:
        value = value.split("://", 1)[1]
        value = value.split("/", 1)[1] if "/" in value else value
    elif ":" in value and not value.startswith(("/", "./", "../")):
        value = value.split(":", 1)[1]
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value.lstrip("/") or None


def _contract_repository(root: Path) -> Optional[str]:
    contract = root / ".ai" / "CONTINUITY.json"
    try:
        data = json.loads(contract.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    repository = data.get("repository") if isinstance(data, dict) else None
    return repository if isinstance(repository, str) and repository.strip() else None


def _repository_identity(root: Path) -> str:
    return _remote_repository(root) or _contract_repository(root) or root.name


def _receipt_repository(receipt: Dict[str, Any]) -> Optional[str]:
    repository = receipt.get("repository")
    if isinstance(repository, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        return repository
    if isinstance(repository, dict):
        owner = repository.get("owner")
        repo = repository.get("repo")
        if isinstance(owner, str) and isinstance(repo, str):
            value = "%s/%s" % (owner, repo)
            if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
                return value
    return None


def _load_source_receipt(path: Path, root: Path, local_tree: str) -> Dict[str, Any]:
    if sync_check is None or not callable(getattr(sync_check, "validate_receipt", None)):
        raise BackupError("sync receipt validator is unavailable")
    if os.path.lexists(str(path)) and path.is_symlink():
        raise BackupError("source receipt must not be a symlink")
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("source receipt is not valid JSON") from exc
    try:
        sync_check.validate_receipt(receipt)
    except Exception as exc:  # pragma: no cover - validator implementation is external
        raise BackupError("source receipt validation failed") from exc
    if not isinstance(receipt, dict):
        raise BackupError("source receipt must be a JSON object")
    receipt_repository = _receipt_repository(receipt)
    contract_repository = _contract_repository(root)
    origin_repository = _remote_repository(root)
    if receipt_repository is None:
        raise BackupError("source receipt repository is invalid")
    if contract_repository is None or receipt_repository != contract_repository:
        raise BackupError("source receipt repository does not match continuity context")
    if origin_repository is not None and receipt_repository != origin_repository:
        raise BackupError("source receipt repository does not match Git origin")
    if receipt.get("expected_tree") != local_tree:
        raise BackupError("source receipt expected_tree does not match local HEAD tree")
    return receipt


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise BackupError("%s must be a non-empty relative path" % label)
    if "\x00" in value or "\\" in value or value.startswith("/") or value.startswith("//"):
        raise BackupError("unsafe path in %s" % label)
    if re.match(r"^[A-Za-z]:", value):
        raise BackupError("unsafe path in %s" % label)
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BackupError("unsafe path in %s" % label)
    if PurePosixPath(value).is_absolute():
        raise BackupError("unsafe path in %s" % label)
    return value


def _payload_path(value: Any, label: str) -> str:
    path = _safe_relative_path(value, label)
    if path in (METADATA_NAME, MANIFEST_NAME):
        raise BackupError("reserved control-file path used as payload")
    lowered = path.lower()
    parts = lowered.split("/")
    name = parts[-1]
    if name == ".env" or (name.startswith(".env.") and name not in {".env.example", ".env.template"}):
        raise BackupError("runtime secret path is not allowed: %s" % path)
    if any(part in _FORBIDDEN_DIRS for part in parts):
        raise BackupError("runtime path is not allowed: %s" % path)
    if any(part in {"secret", "secrets", "credential", "credentials"} for part in parts):
        raise BackupError("runtime secret path is not allowed: %s" % path)
    if name in _FORBIDDEN_NAMES or name.endswith(_FORBIDDEN_SUFFIXES):
        raise BackupError("runtime secret or database path is not allowed: %s" % path)
    return path


def _reject_symlinks(root: Path) -> None:
    for base, directories, files in os.walk(str(root), topdown=True, followlinks=False):
        base_path = Path(base)
        kept_directories = []
        for name in directories:
            path = base_path / name
            if name == ".git":
                continue
            if path.is_symlink():
                raise BackupError("symlink is not allowed: %s" % path.relative_to(root))
            kept_directories.append(name)
        directories[:] = kept_directories
        for name in files:
            path = base_path / name
            if path.is_symlink():
                raise BackupError("symlink is not allowed: %s" % path.relative_to(root))


def _tracked_payloads(root: Path) -> List[Tuple[str, bytes, int]]:
    raw = _git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD")
    payloads: List[Tuple[str, bytes, int]] = []
    seen = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            header, path_bytes = record.split(b"\t", 1)
            mode, object_type, _object_id = header.decode("ascii").split(" ", 2)
            relative = path_bytes.decode("utf-8", "strict")
        except (UnicodeDecodeError, ValueError) as exc:
            raise BackupError("Git contains an unreadable tracked path") from exc
        if relative in seen:
            raise BackupError("Git returned a duplicate tracked path")
        seen.add(relative)
        if mode == "120000":
            raise BackupError("tracked symlink is not allowed: %s" % relative)
        if mode not in ("100644", "100755") or object_type != "blob":
            raise BackupError("unsupported tracked entry: %s" % relative)
        if not _SHA_RE.fullmatch(_object_id):
            raise BackupError("Git returned an invalid blob SHA")
        relative = _payload_path(relative, "tracked path")
        source = root.joinpath(*relative.split("/"))
        if source.is_symlink() or not source.is_file():
            raise BackupError("tracked file is missing or is a symlink: %s" % relative)
        try:
            resolved = source.resolve(strict=True)
        except OSError as exc:
            raise BackupError("cannot resolve tracked file: %s" % relative) from exc
        if not _is_within(resolved, root):
            raise BackupError("tracked file escapes the repository: %s" % relative)
        try:
            mode_bits = source.stat().st_mode
            data = _git(root, "cat-file", "blob", _object_id)
        except (OSError, BackupError) as exc:
            raise BackupError("cannot read tracked file: %s" % relative) from exc
        if not stat.S_ISREG(mode_bits) or source.is_symlink():
            raise BackupError("tracked entry is not a regular file: %s" % relative)
        payloads.append((relative, data, 0o755 if mode == "100755" else 0o644))
    return sorted(payloads, key=lambda item: item[0])


def _run_security_scan(root: Path) -> None:
    if security is None:
        raise BackupError("security scanner is unavailable")
    scanner = getattr(security, "scan", None)
    if not callable(scanner):
        raise BackupError("security scanner is unavailable")
    try:
        issues = scanner(root)
    except Exception as exc:  # pragma: no cover - scanner implementation is external
        raise BackupError("security scan failed") from exc
    if issues:
        try:
            count = len(issues)
        except TypeError:
            count = 1
        raise BackupError("security scan rejected the repository (%d issue(s))" % count)


def _validate_context(root: Path) -> None:
    if context is None:
        raise BackupError("continuity context validator is unavailable")
    validator = getattr(context, "validate_context", None)
    if not callable(validator):
        raise BackupError("continuity context validator is unavailable")
    try:
        valid = validator(root)
    except Exception as exc:  # pragma: no cover - validator implementation is external
        raise BackupError("mapped continuity context is invalid") from exc
    if valid is not True:
        raise BackupError("mapped continuity context is invalid")


def _json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _zip_info(name: str, file_mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | file_mode) << 16
    return info


def _created_at(value: Any = None) -> str:
    if value is None:
        current = _datetime.datetime.now(_datetime.timezone.utc)
    elif isinstance(value, _datetime.datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=_datetime.timezone.utc)
    elif isinstance(value, str):
        current = _parse_created_at(value)
    else:
        raise BackupError("created_at must be an ISO timestamp")
    current = current.astimezone(_datetime.timezone.utc).replace(microsecond=0)
    return current.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_new_output(root: Path, output: Path) -> Path:
    target = Path(output).expanduser()
    resolved = target.resolve(strict=False)
    if _is_within(resolved, root):
        raise BackupError("backup output must be outside the repository root")
    if os.path.lexists(str(target)):
        raise BackupError("backup output must be a new path")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError("cannot create the backup output directory") from exc
    return target


def pack(
    root: Path,
    output: Path,
    created_at: Any = None,
    source_receipt: Optional[Path] = None,
) -> Path:
    """Pack a clean repository HEAD into a new deterministic ZIP archive."""

    repository_root = _repository_root(Path(root))
    target = _ensure_new_output(repository_root, Path(output))
    _require_clean(repository_root)
    _reject_symlinks(repository_root)
    _validate_context(repository_root)
    _run_security_scan(repository_root)
    local_commit, tree = _commit_and_tree(repository_root)
    metadata_commit = local_commit
    provenance_local_commit = None
    if source_receipt is not None:
        receipt = _load_source_receipt(Path(source_receipt).expanduser(), repository_root, tree)
        metadata_commit = receipt["remote_commit"]
        provenance_local_commit = local_commit
    payloads = _tracked_payloads(repository_root)
    if _commit_and_tree(repository_root) != (local_commit, tree):
        raise BackupError("repository HEAD changed during backup preparation")
    _require_clean(repository_root)

    manifest_files = [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        for path, data, _file_mode in payloads
    ]
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "kind": BACKUP_KIND,
        "repository": _repository_identity(repository_root),
        "commit": metadata_commit,
        "tree": tree,
        "created_at": _created_at(created_at),
        "protected": False,
        "production": False,
        "formal": False,
        "milestone": False,
    }
    if provenance_local_commit is not None:
        metadata["provenance_local_commit"] = provenance_local_commit
    manifest = {"schema_version": SCHEMA_VERSION, "files": manifest_files}

    try:
        with target.open("xb") as handle:
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                archive.writestr(_zip_info(METADATA_NAME), _json_bytes(metadata))
                archive.writestr(_zip_info(MANIFEST_NAME), _json_bytes(manifest))
                archive.writestr(_zip_info(RESTORE_NAME), RESTORE_INSTRUCTIONS)
                for path, data, file_mode in payloads:
                    archive.writestr(_zip_info(path, file_mode), data)
    except (OSError, zipfile.BadZipFile) as exc:
        try:
            target.unlink()
        except OSError:
            pass
        raise BackupError("could not write backup archive") from exc
    return target


def _archive_path(archive: Path) -> Path:
    path = Path(archive).expanduser()
    if not os.path.lexists(str(path)):
        raise BackupError("archive does not exist")
    if path.is_symlink() or not path.is_file():
        raise BackupError("archive must be a regular file, not a symlink")
    return path.resolve()


def _validate_zip_members(archive: zipfile.ZipFile) -> Dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBER_COUNT:
        raise BackupError("archive contains too many members")
    members: Dict[str, zipfile.ZipInfo] = {}
    total_size = 0
    for info in infos:
        name = _safe_relative_path(info.filename, "archive member")
        if name in members:
            raise BackupError("archive contains duplicate member: %s" % name)
        mode = (info.external_attr >> 16) & 0xFFFF
        if info.is_dir() or name.endswith("/") or stat.S_ISDIR(mode):
            raise BackupError("archive directories are not allowed: %s" % name)
        if stat.S_ISLNK(mode):
            raise BackupError("archive symlink is not allowed: %s" % name)
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
            raise BackupError("archive special file is not allowed: %s" % name)
        if mode & 0o7000:
            raise BackupError("archive special permission is not allowed: %s" % name)
        if info.flag_bits & 0x1:
            raise BackupError("encrypted archive member is not allowed: %s" % name)
        if info.file_size > MAX_MEMBER_BYTES:
            raise BackupError("archive member is too large: %s" % name)
        total_size += info.file_size
        if total_size > MAX_TOTAL_BYTES:
            raise BackupError("archive uncompressed size is too large")
        if info.file_size and not info.compress_size:
            raise BackupError("archive member has invalid compression size: %s" % name)
        if (
            info.file_size > 1024 * 1024
            and info.file_size > max(info.compress_size, 1) * MAX_COMPRESSION_RATIO
        ):
            raise BackupError("archive compression ratio is unsafe: %s" % name)
        members[name] = info
    return members


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    data = bytearray()
    try:
        with archive.open(info, "r") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                data.extend(chunk)
                if len(data) > MAX_MEMBER_BYTES:
                    raise BackupError("archive member is too large")
    except BackupError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, KeyError) as exc:
        raise BackupError("archive member cannot be read") from exc
    if len(data) != info.file_size:
        raise BackupError("archive member size does not match its ZIP header")
    return bytes(data)


def _member_mode(info: zipfile.ZipInfo) -> int:
    mode = (info.external_attr >> 16) & 0o777
    return 0o755 if mode & 0o111 else 0o644


def _json_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, name: str) -> Any:
    data = _read_member(archive, info)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("%s is not valid UTF-8 JSON" % name) from exc


def _parse_created_at(value: Any) -> _datetime.datetime:
    if not isinstance(value, str) or not value:
        raise BackupError("metadata created_at is invalid")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = _datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise BackupError("metadata created_at is invalid") from exc
    if parsed.tzinfo is None:
        raise BackupError("metadata created_at must include a timezone")
    return parsed.astimezone(_datetime.timezone.utc)


def _validate_metadata(metadata: Any) -> _datetime.datetime:
    if not isinstance(metadata, dict):
        raise BackupError("backup metadata must be an object")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise BackupError("unsupported backup metadata schema")
    if metadata.get("kind") != BACKUP_KIND:
        raise BackupError("archive is not an ordinary continuity backup")
    if not isinstance(metadata.get("repository"), str) or not metadata["repository"].strip():
        raise BackupError("metadata repository is invalid")
    for key in ("commit", "tree"):
        if not isinstance(metadata.get(key), str) or not _SHA_RE.fullmatch(metadata[key]):
            raise BackupError("metadata %s is not a full Git SHA" % key)
    for key in ("protected", "production", "formal", "milestone"):
        if type(metadata.get(key)) is not bool:
            raise BackupError("metadata %s must be boolean" % key)
    if "provenance_local_commit" in metadata:
        if not isinstance(metadata["provenance_local_commit"], str) or not _SHA_RE.fullmatch(
            metadata["provenance_local_commit"]
        ):
            raise BackupError("metadata provenance_local_commit is not a full Git SHA")
    return _parse_created_at(metadata.get("created_at"))


def _manifest_records(manifest: Any) -> List[Dict[str, Any]]:
    if isinstance(manifest, dict):
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise BackupError("unsupported manifest schema")
        records = manifest.get("files")
    elif isinstance(manifest, list):
        records = manifest
    else:
        records = None
    if not isinstance(records, list):
        raise BackupError("manifest files must be a list")
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in records:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise BackupError("manifest entry has an invalid shape")
        path = _payload_path(item["path"], "manifest path")
        if path in seen:
            raise BackupError("manifest contains duplicate path: %s" % path)
        seen.add(path)
        if type(item["size"]) is not int or item["size"] < 0:
            raise BackupError("manifest size is invalid: %s" % path)
        if not isinstance(item["sha256"], str) or not _HASH_RE.fullmatch(item["sha256"]):
            raise BackupError("manifest hash is invalid: %s" % path)
        result.append({"path": path, "size": item["size"], "sha256": item["sha256"].lower()})
    return result


def _inspect_open_archive(
    archive: zipfile.ZipFile,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, zipfile.ZipInfo], Dict[str, bytes]]:
    members = _validate_zip_members(archive)
    for required in (METADATA_NAME, MANIFEST_NAME, RESTORE_NAME):
        if required not in members:
            raise BackupError("archive is missing %s" % required)
    if _read_member(archive, members[RESTORE_NAME]) != RESTORE_INSTRUCTIONS:
        raise BackupError("restore instructions are missing or modified")
    metadata = _json_member(archive, members[METADATA_NAME], METADATA_NAME)
    _validate_metadata(metadata)
    manifest = _json_member(archive, members[MANIFEST_NAME], MANIFEST_NAME)
    records = _manifest_records(manifest)
    listed = {record["path"] for record in records}
    actual = set(members) - {METADATA_NAME, MANIFEST_NAME, RESTORE_NAME}
    if actual != listed:
        raise BackupError("manifest membership does not exactly match archive payload")
    payload_data: Dict[str, bytes] = {}
    for record in records:
        info = members[record["path"]]
        data = _read_member(archive, info)
        if len(data) != record["size"]:
            raise BackupError("manifest size mismatch: %s" % record["path"])
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise BackupError("manifest hash mismatch: %s" % record["path"])
        payload_data[record["path"]] = data
    return metadata, records, members, payload_data


def _scan_verified_payload(
    records: List[Dict[str, Any]],
    members: Dict[str, zipfile.ZipInfo],
    payload_data: Dict[str, bytes],
) -> None:
    with tempfile.TemporaryDirectory(prefix="continuity-backup-verify-") as temporary:
        root = Path(temporary)
        for record in records:
            target = root.joinpath(*record["path"].split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(payload_data[record["path"]])
            target.chmod(_member_mode(members[record["path"]]))
        _validate_context(root)
        _run_security_scan(root)


def verify(archive: Path) -> Dict[str, Any]:
    """Verify all control data, members, and payload hashes in an archive."""

    path = _archive_path(Path(archive))
    try:
        with zipfile.ZipFile(path, "r") as opened:
            metadata, records, members, payload_data = _inspect_open_archive(opened)
            _scan_verified_payload(records, members, payload_data)
    except BackupError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        raise BackupError("archive verification failed") from exc
    return {"verified": True, "metadata": metadata, "files": records}


def _restore_target(destination: Path, relative: str) -> Path:
    target = destination.joinpath(*relative.split("/"))
    destination_resolved = destination.resolve(strict=True)
    target_resolved = target.resolve(strict=False)
    if not _is_within(target_resolved, destination_resolved):
        raise BackupError("archive path escapes restore destination")
    parent = target.parent
    while True:
        if parent.is_symlink():
            raise BackupError("restore destination contains a symlink")
        if parent == destination:
            break
        parent = parent.parent
    return target


def restore(archive: Path, destination: Path) -> Path:
    """Restore payload files into a new destination directory without overwrite."""

    archive_path = _archive_path(Path(archive))
    target_root = Path(destination).expanduser()
    if os.path.lexists(str(target_root)):
        raise BackupError("restore destination must not already exist")
    try:
        target_root.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError("cannot create restore parent directory") from exc

    created = False
    try:
        with zipfile.ZipFile(archive_path, "r") as opened:
            _metadata, records, members, payload_data = _inspect_open_archive(opened)
            _scan_verified_payload(records, members, payload_data)
            target_root.mkdir()
            created = True
            for record in records:
                target = _restore_target(target_root, record["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if os.path.lexists(str(target)):
                    raise BackupError("restore would overwrite: %s" % record["path"])
                data = payload_data[record["path"]]
                with target.open("xb") as handle:
                    handle.write(data)
                target.chmod(_member_mode(members[record["path"]]))
    except BackupError:
        if created:
            shutil.rmtree(str(target_root), ignore_errors=True)
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
        if created:
            shutil.rmtree(str(target_root), ignore_errors=True)
        raise BackupError("restore failed") from exc
    return target_root


def _inventory_records(inventory: Any) -> Tuple[List[Any], Optional[str]]:
    if isinstance(inventory, list):
        return inventory, None
    if not isinstance(inventory, dict):
        raise BackupError("inventory JSON must be a list or object")
    top_repository = inventory.get("repository", inventory.get("repo"))
    for key in ("records", "backups", "releases", "items"):
        if isinstance(inventory.get(key), list):
            return inventory[key], top_repository
    if any(key in inventory for key in ("release_id", "id", "tag_name", "tag")):
        return [inventory], top_repository
    raise BackupError("inventory object has no records list")


def _record_id(record: Dict[str, Any]) -> Optional[int]:
    values = []
    for key in ("release_id", "id"):
        if key in record:
            value = record[key]
            if type(value) is not int or value <= 0:
                return None
            values.append(value)
    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _record_tag(record: Dict[str, Any]) -> Optional[str]:
    values = []
    for key in ("tag_name", "tag", "release_tag"):
        if key in record:
            if not isinstance(record[key], str):
                return None
            values.append(record[key])
    if not values or len(set(values)) != 1:
        return None
    return values[0]


def _record_repository(record: Dict[str, Any], top_repository: Optional[str]) -> Optional[str]:
    if "repository" in record:
        return record["repository"] if isinstance(record["repository"], str) else None
    if "repo" in record:
        return record["repo"] if isinstance(record["repo"], str) else None
    return top_repository


def _record_excluded(record: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    for source in (record, metadata):
        for key in ("formal", "production", "milestone", "protected", "draft"):
            if key in source and (type(source[key]) is not bool or source[key]):
                return True
        status = source.get("status")
        if status is not None:
            if not isinstance(status, str) or status.lower() not in {
                "active",
                "complete",
                "published",
                "released",
                "verified",
            }:
                return True
    return False


def _retention_repository(records: List[Any], top_repository: Optional[str]) -> Optional[str]:
    if top_repository is not None:
        if not isinstance(top_repository, str) or not top_repository.strip():
            return None
        return top_repository

    repositories = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if not any(key in record for key in ("release_id", "id", "tag_name", "tag", "release_tag")):
            continue
        if _record_id(record) is None or _record_tag(record) is None:
            continue
        repository = _record_repository(record, None)
        if not isinstance(repository, str) or not repository.strip():
            return None
        repositories.append(repository)
    if not repositories or len(set(repositories)) != 1:
        return None
    return repositories[0]


def retention_plan(inventory: Any) -> List[int]:
    """Return eligible release IDs older than the newest three.

    This is a pure calculation.  Malformed, ambiguous, unverified, or
    protected records are ignored and can never be returned for deletion.
    """

    records, top_repository = _inventory_records(inventory)
    target_repository = _retention_repository(records, top_repository)
    if target_repository is None:
        return []
    candidates: List[Tuple[_datetime.datetime, int]] = []
    occurrences: Dict[int, int] = {}
    for raw_record in records:
        if not isinstance(raw_record, dict):
            continue
        release_id = _record_id(raw_record)
        tag = _record_tag(raw_record)
        if release_id is None or tag is None:
            continue
        occurrences[release_id] = occurrences.get(release_id, 0) + 1
        match = _TAG_RE.fullmatch(tag)
        if not match or raw_record.get("verified") is not True:
            continue
        metadata = raw_record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        try:
            created = _validate_metadata(metadata)
        except BackupError:
            continue
        expected_repository = _record_repository(raw_record, top_repository)
        if expected_repository != target_repository or metadata.get("repository") != target_repository:
            continue
        try:
            tag_time = _datetime.datetime.strptime(
                "%s-%s" % (match.group(1), match.group(2)), "%Y%m%d-%H%M%S"
            ).replace(tzinfo=_datetime.timezone.utc)
        except ValueError:
            continue
        if created != tag_time or _record_excluded(raw_record, metadata):
            continue
        if "release_id" in metadata and metadata["release_id"] != release_id:
            continue
        for key in ("tag", "tag_name"):
            if key in metadata and metadata[key] != tag:
                break
        else:
            candidates.append((tag_time, release_id))

    eligible = [item for item in candidates if occurrences.get(item[1]) == 1]
    eligible.sort(key=lambda item: (item[0], item[1]))
    if len(eligible) <= RETENTION_KEEP:
        return []
    return [release_id for _tag_time, release_id in eligible[:-RETENTION_KEEP]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Deterministic continuity backup utility")
    commands = parser.add_subparsers(dest="command")

    pack_parser = commands.add_parser("pack")
    pack_parser.add_argument("--root", required=True, type=Path)
    pack_parser.add_argument("--output", required=True, type=Path)
    pack_parser.add_argument("--source-receipt", type=Path)
    pack_parser.add_argument("--created-at", help="UTC ISO timestamp for reproducible test snapshots")

    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--archive", required=True, type=Path)

    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--archive", required=True, type=Path)
    restore_parser.add_argument("--destination", required=True, type=Path)

    retention_parser = commands.add_parser("retention-plan")
    inventory_source = retention_parser.add_mutually_exclusive_group(required=True)
    inventory_source.add_argument("--inventory", help="inventory JSON")
    inventory_source.add_argument("--inventory-file", type=Path, help="inventory JSON file")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("a command is required")
    try:
        if args.command == "pack":
            print(str(pack(args.root, args.output, created_at=args.created_at, source_receipt=args.source_receipt)))
        elif args.command == "verify":
            print(json.dumps(verify(args.archive), ensure_ascii=False, sort_keys=True))
        elif args.command == "restore":
            print(str(restore(args.archive, args.destination)))
        elif args.command == "retention-plan":
            try:
                inventory = json.loads(args.inventory if args.inventory is not None else args.inventory_file.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise BackupError("inventory must contain valid JSON") from exc
            print(json.dumps(retention_plan(inventory), ensure_ascii=False))
        else:  # pragma: no cover - argparse restricts this branch
            parser.error("unknown command")
    except (BackupError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
