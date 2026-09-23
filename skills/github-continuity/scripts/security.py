#!/usr/bin/env python3
"""Conservative, value-redacting audit for a continuity repository.

The public API deliberately returns only relative/display paths and categories.
It never returns a matched value, a line of source, or git object contents.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union


Finding = Dict[str, str]
PathLike = Union[str, os.PathLike]
ReviewKey = Tuple[str, str]
ReviewInput = Union[Mapping[str, Mapping[str, str]], Sequence[Mapping[str, str]]]

SAFE_ENV_NAMES = frozenset({".env.example", ".env.template"})
FORBIDDEN_DIR_NAMES = frozenset(
    {
        ".ssh",
        "ssh",
        "session",
        "sessions",
        "cookie",
        "cookies",
        "credential",
        "credentials",
        "keyring",
        "db",
        "runtime",
        "backup",
        "backups",
        "log",
        "logs",
        "upload",
        "uploads",
    }
)
SENSITIVE_FILE_NAMES = frozenset(
    {
        ".npmrc",
        ".pypirc",
        "authorized_keys",
        "credentials",
        "credentials.json",
        "credential",
        "credential.json",
        "cookie",
        "cookies",
        "cookies.json",
        "cookiejar",
        "known_hosts",
        "session",
        "session.json",
        "sessions.json",
        "token.json",
        "tokens.json",
        "auth.json",
        "oauth.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
    }
)
SENSITIVE_SUFFIXES = (
    ".cookie",
    ".cookies",
    ".session",
    ".db",
    ".db-journal",
    ".sqlite",
    ".sqlite3",
    ".sqlite-wal",
    ".sqlite-shm",
    ".log",
    ".key",
    ".p12",
    ".pfx",
)
ARCHIVE_SUFFIXES = (
    ".7z",
    ".bz2",
    ".gz",
    ".gzip",
    ".rar",
    ".tar",
    ".tar.bz2",
    ".tar.gz",
    ".tar.xz",
    ".tgz",
    ".xz",
    ".zip",
)
BINARY_SUFFIXES = (
    ".avif",
    ".bmp",
    ".bin",
    ".class",
    ".dylib",
    ".der",
    ".dll",
    ".exe",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".otf",
    ".pdf",
    ".png",
    ".pyc",
    ".so",
    ".tif",
    ".tiff",
    ".ttf",
    ".wasm",
    ".webp",
    ".woff",
    ".woff2",
)
STATIC_ASSET_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    ".avif": (b"ftypavif", b"ftypavis"),
    ".bmp": (b"BM",),
    ".gif": (b"GIF87a", b"GIF89a"),
    ".ico": (b"\x00\x00\x01\x00",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".jpg": (b"\xff\xd8\xff",),
    ".otf": (b"OTTO",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".tif": (b"II*\x00", b"MM\x00*"),
    ".tiff": (b"II*\x00", b"MM\x00*"),
    ".ttf": (b"\x00\x01\x00\x00", b"true", b"typ1"),
    ".webp": (),
    ".woff": (b"wOFF",),
    ".woff2": (b"wOF2",),
}
STATIC_ASSET_REVIEW_MANIFEST = ".ai/STATIC_ASSET_REVIEWS.json"
STATIC_ASSET_REVIEW_FIELDS = frozenset({"path", "sha256", "reviewer", "reason"})
REVIEW_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
SQLITE_SIGNATURE = b"SQLite format 3\x00"
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
MAX_ARCHIVE_DEPTH = 8
MAX_ARCHIVE_MEMBERS = 256
MAX_ARCHIVE_MEMBER_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 32 * 1024 * 1024

PRIVATE_KEY = re.compile(
    r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----", re.IGNORECASE
)
SECRET_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("openai_like_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    (
        "github_token",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    ),
    (
        "stripe_live_key",
        re.compile(r"\b(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}\b"),
    ),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.IGNORECASE)),
    ("oauth_token", re.compile(r"\b(?:ya29\.|1//)[A-Za-z0-9._/-]{20,}\b")),
    (
        "cloud_credential",
        re.compile(
            r"\bDefaultEndpointsProtocol=[^;\s]+;AccountName=[^;\s]+;"
            r"AccountKey=[^;\s]+(?:;[^\s]+)*",
            re.IGNORECASE,
        ),
    ),
)
ASSIGNED_SECRET = re.compile(
    r"""(?ix)
    (?P<name>
        api[_-]?key|access[_-]?key|secret(?:[_-]?(?:key|access[_-]?key))?|
        token|password|passwd|pwd|private[_-]?key|client[_-]?secret|
        access[_-]?token|refresh[_-]?token|oauth[_-]?(?:token|secret)|
        aws[_-]?secret[_-]?access[_-]?key|azure[_-]?(?:key|secret)|
        gcp[_-]?(?:key|secret)|openai[_-]?key|deepseek[_-]?key
    )
    \s*(?:=|:)\s*
    (?P<value>
        "(?:[^"\\\r\n]|\\.)*" |
        '(?:[^'\\\r\n]|\\.)*' |
        \$\{[^}\r\n]+\} |
        <[^>\r\n]+> |
        [^\s,;}\]\\"']+
    )
    """
)
URL_SECRET = re.compile(
    r"""(?ix)(?:[?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|password)=)
    (?P<value>[^&#\s]{8,})"""
)
PLACEHOLDER_VALUE = re.compile(
    r"""(?ix)\A(?:
        YOUR_[A-Z0-9_]+(?:_HERE)?
        |\$\{[A-Z_][A-Z0-9_]*\}
        |<[^>\r\n]+>
        |(?:dummy|fixture|mock|placeholder|synthetic|sample|fake|redacted|
           test[-_ ]only|not[-_ ]a[-_ ]real)(?:[-_ ][A-Z0-9_-]+)*
    )\Z"""
)
NON_VALUE = frozenset(
    {
        "false",
        "null",
        "none",
        "true",
        "undefined",
        "unset",
    }
)


def _display_path(path: PathLike) -> str:
    """Return a one-line path safe to include in an audit finding."""

    value = os.fspath(path)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    value = str(value).replace("\\", "/")
    return "".join(character if ord(character) >= 0x20 and character != "\x7f" else "?" for character in value)


def _finding(path: PathLike, category: str) -> Finding:
    return {"path": _display_path(path), "category": category}


def _unique(findings: Iterable[Finding]) -> List[Finding]:
    seen: Set[Tuple[str, str]] = set()
    result: List[Finding] = []
    for item in findings:
        key = (item["path"], item["category"])
        if key not in seen:
            seen.add(key)
            result.append({"path": key[0], "category": key[1]})
    return sorted(result, key=lambda item: (item["path"], item["category"]))


def _forbidden_path(path: PathLike) -> bool:
    """Return whether a path names data that must not enter continuity."""

    parts = [part.lower() for part in _display_path(path).strip("/").split("/") if part]
    if not parts:
        return False
    if any(part == ".git" for part in parts):
        return False
    for part in parts:
        if part.startswith(".env") and part not in SAFE_ENV_NAMES:
            return True
    if any(part in FORBIDDEN_DIR_NAMES for part in parts):
        return True
    name = parts[-1]
    if name in SENSITIVE_FILE_NAMES:
        return True
    if re.fullmatch(r"id_(?:dsa|ecdsa|ed25519|rsa)", name):
        return True
    if re.search(r"(?:^|[._-])(?:private|secret|key)(?:[._-]|$)", name) and name.endswith(
        (".pem", ".crt", ".cer", ".key", ".p12", ".pfx")
    ):
        return True
    if any(name.endswith(suffix) for suffix in SENSITIVE_SUFFIXES):
        return True
    return False


def _is_placeholder_value(value: str) -> bool:
    """Allow only a complete, explicit placeholder value."""

    return PLACEHOLDER_VALUE.fullmatch(value.strip()) is not None


def _scan_text(path: PathLike, text: str) -> List[Finding]:
    findings: List[Finding] = []
    matched_ranges: List[Tuple[int, int]] = []

    for match in PRIVATE_KEY.finditer(text):
        findings.append(_finding(path, "private_key"))
        matched_ranges.append((match.start(), match.end()))

    for category, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            findings.append(_finding(path, category))
            matched_ranges.append((match.start(), match.end()))

    for match in ASSIGNED_SECRET.finditer(text):
        raw_value = match.group("value")
        value = raw_value[1:-1] if len(raw_value) >= 2 and raw_value[0] in "\"'" and raw_value[-1] == raw_value[0] else raw_value
        if len(value.strip()) < 8 or value.strip().lower() in NON_VALUE:
            continue
        if re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\s*\(", value.strip()):
            continue
        if _is_placeholder_value(value):
            continue
        if any(start < match.end() and match.start() < end for start, end in matched_ranges):
            continue
        findings.append(_finding(path, "assigned_secret"))

    for match in URL_SECRET.finditer(text):
        if _is_placeholder_value(match.group("value")):
            continue
        findings.append(_finding(path, "assigned_secret"))

    return findings


def _archive_kind(path: PathLike, data: bytes) -> Optional[str]:
    name = _display_path(path).lower()
    if data.startswith(ZIP_SIGNATURES) or name.endswith(".zip"):
        return "zip"
    if (
        data.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00", b"7z\xbc\xaf\x27\x1c", b"Rar!\x1a\x07"))
        or (len(data) >= 262 and data[257:262] == b"ustar")
        or name.endswith(ARCHIVE_SUFFIXES[:-1])
    ):
        return "unknown"
    return None


def _decode_text(data: bytes) -> Optional[str]:
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _safe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    if not normalized or normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        return False
    if "\\" in name:
        return False
    parts = normalized.split("/")
    if normalized.endswith("/"):
        parts = parts[:-1]
    return bool(parts) and all(part not in {"", ".", ".."} for part in parts)


def _member_path(archive_path: PathLike, member_name: str) -> str:
    return "{}!/{!s}".format(_display_path(archive_path), PurePosixPath(member_name).as_posix())


def _scan_zip(path: PathLike, data: bytes, depth: int) -> List[Finding]:
    if depth >= MAX_ARCHIVE_DEPTH:
        return [_finding(path, "blocked_archive")]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (OSError, ValueError, zipfile.BadZipFile):
        return [_finding(path, "blocked_archive")]

    findings: List[Finding] = []
    with archive:
        try:
            infos = archive.infolist()
        except (OSError, ValueError, zipfile.BadZipFile):
            return [_finding(path, "blocked_archive")]
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            return [_finding(path, "blocked_archive")]
        declared_total = 0
        for info in infos:
            if info.file_size < 0 or info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                return [_finding(path, "blocked_archive")]
            declared_total += info.file_size
            if declared_total > MAX_ARCHIVE_TOTAL_BYTES:
                return [_finding(path, "blocked_archive")]
        if len(archive.comment) > MAX_ARCHIVE_MEMBER_BYTES:
            return [_finding(path, "blocked_archive")]
        if archive.comment:
            findings.extend(_scan_bytes(_member_path(path, "__archive_comment__"), archive.comment, depth + 1))
        actual_total = 0
        for info in infos:
            name = info.filename
            member = _member_path(path, name) if name else _display_path(path) + "!/"
            if not _safe_zip_member(name):
                findings.append(_finding(member, "unsafe_archive_path"))
                continue
            if _forbidden_path(name) or ".git" in {part.lower() for part in name.replace("\\", "/").split("/") if part}:
                findings.append(_finding(member, "forbidden_path"))
            mode = (info.external_attr >> 16) & 0o170000
            if stat.S_ISLNK(mode):
                findings.append(_finding(member, "symlink"))
                continue
            if info.is_dir():
                continue
            try:
                member_data = archive.read(info)
            except (EOFError, OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile):
                findings.append(_finding(member, "blocked_archive"))
                continue
            actual_total += len(member_data)
            if len(member_data) > MAX_ARCHIVE_MEMBER_BYTES or actual_total > MAX_ARCHIVE_TOTAL_BYTES:
                findings.append(_finding(member, "blocked_archive"))
                continue
            findings.extend(_scan_bytes(member, member_data, depth + 1))
    return findings


def _canonical_review_path(path: PathLike) -> Optional[str]:
    """Return a canonical relative POSIX path eligible for a review record."""

    value = os.fspath(path)
    if isinstance(value, bytes):
        value = os.fsdecode(value)
    value = str(value)
    if "\\" in value or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        return None
    if value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        return None
    parts = value.split("/")
    if not parts or any(part in {"", ".", "..", ".git"} for part in parts):
        return None
    suffix = PurePosixPath(value).suffix.lower()
    if suffix not in STATIC_ASSET_SIGNATURES or _forbidden_path(value):
        return None
    return value


def _valid_review_record(path: object, review: object) -> bool:
    if not isinstance(path, str) or _canonical_review_path(path) != path:
        return False
    if not isinstance(review, Mapping) or set(review) != {"sha256", "reviewer", "reason"}:
        return False
    digest = review.get("sha256")
    reviewer = review.get("reviewer")
    reason = review.get("reason")
    return (
        isinstance(digest, str)
        and REVIEW_SHA256.fullmatch(digest) is not None
        and isinstance(reviewer, str)
        and bool(reviewer.strip())
        and isinstance(reason, str)
        and bool(reason.strip())
    )


def _unique_json_object(pairs: List[Tuple[str, object]]) -> Dict[str, object]:
    result: Dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _load_static_asset_reviews(root: Path) -> Tuple[Set[ReviewKey], bool]:
    """Read the optional list of exact path/hash/reviewer/reason records; fail closed."""

    review_dir = root / ".ai"
    manifest = root / STATIC_ASSET_REVIEW_MANIFEST
    try:
        directory_mode = review_dir.lstat().st_mode
    except FileNotFoundError:
        return set(), False
    except OSError:
        return set(), True
    if not stat.S_ISDIR(directory_mode):
        return set(), True

    try:
        manifest_mode = manifest.lstat().st_mode
    except FileNotFoundError:
        return set(), False
    except OSError:
        return set(), True
    if not stat.S_ISREG(manifest_mode):
        return set(), True

    try:
        payload = json.loads(
            manifest.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_json_object,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        return set(), True
    if not isinstance(payload, list):
        return set(), True

    reviews: Set[ReviewKey] = set()
    for item in payload:
        if not isinstance(item, dict) or set(item) != STATIC_ASSET_REVIEW_FIELDS:
            return set(), True
        path = item.get("path")
        review = {key: item.get(key) for key in ("sha256", "reviewer", "reason")}
        if not _valid_review_record(path, review):
            return set(), True
        key = (path, review["sha256"].lower())
        if key in reviews:
            return set(), True
        reviews.add(key)
    return reviews, False


def _reviews_for_root(
    root: Path,
    supplied: Optional[ReviewInput],
) -> Tuple[Set[ReviewKey], bool]:
    reviews, invalid = _load_static_asset_reviews(root)
    if invalid:
        return set(), True
    if supplied is None:
        return reviews, False
    if isinstance(supplied, Mapping):
        entries = supplied.items()
    elif isinstance(supplied, (list, tuple)):
        if any(not isinstance(item, Mapping) or set(item) != STATIC_ASSET_REVIEW_FIELDS for item in supplied):
            return set(), True
        entries = (
            (item["path"], {key: item[key] for key in ("sha256", "reviewer", "reason")})
            for item in supplied
        )
    else:
        return set(), True
    for path, review in entries:
        if not _valid_review_record(path, review):
            return set(), True
        key = (path, review["sha256"].lower())
        if key in reviews:
            return set(), True
        reviews.add(key)
    return reviews, False


def _has_static_asset_signature(path: str, data: bytes) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if suffix == ".avif":
        return len(data) >= 12 and data[4:12] in STATIC_ASSET_SIGNATURES[suffix]
    return any(data.startswith(signature) for signature in STATIC_ASSET_SIGNATURES[suffix])


def _has_static_asset_review(
    path: PathLike,
    data: bytes,
    reviews: Optional[Set[ReviewKey]],
    review_path: Optional[PathLike] = None,
) -> bool:
    """Accept only an exact-path, exact-content, attributed static-asset review."""

    canonical_path = _canonical_review_path(path if review_path is None else review_path)
    if canonical_path is None or reviews is None:
        return False
    if (
        not _has_static_asset_signature(canonical_path, data)
        or data.startswith(SQLITE_SIGNATURE)
    ):
        return False
    return (canonical_path, hashlib.sha256(data).hexdigest()) in reviews


def _scan_bytes(
    path: PathLike,
    data: bytes,
    depth: int = 0,
    reviewed_static_assets: Optional[Set[ReviewKey]] = None,
    review_path: Optional[PathLike] = None,
) -> List[Finding]:
    findings: List[Finding] = []
    if _forbidden_path(path):
        findings.append(_finding(path, "forbidden_path"))

    archive_kind = _archive_kind(path, data)
    if archive_kind == "zip":
        findings.extend(_scan_zip(path, data, depth))
        return findings
    if archive_kind == "unknown":
        findings.append(_finding(path, "blocked_archive"))
        return findings
    if _display_path(path).lower().endswith(BINARY_SUFFIXES):
        if data.startswith(SQLITE_SIGNATURE):
            findings.append(_finding(path, "database_content"))
        if _has_static_asset_review(path, data, reviewed_static_assets, review_path):
            text_findings = _scan_text(path, data.decode("latin-1"))
            if not text_findings:
                return findings
            findings.extend(text_findings)
        findings.append(_finding(path, "blocked_binary"))
        return findings

    text = _decode_text(data)
    if text is None:
        findings.append(_finding(path, "blocked_binary"))
    else:
        findings.extend(_scan_text(path, text))
    return findings


def scan_bytes(path: PathLike, data: bytes) -> List[Finding]:
    """Scan one file or archive and return only redacted path/category findings."""

    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    return _unique(_scan_bytes(path, bytes(data)))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def scan(
    root: PathLike,
    reviewed_static_assets: Optional[ReviewInput] = None,
) -> List[Finding]:
    """Scan working files below *root*, excluding every directory named .git.

    Symlinks are reported but never followed. Unknown binary and unsupported
    archive content is blocked. Optional asset reviews load from .ai/STATIC_ASSET_REVIEWS.json.
    """

    root_input = Path(root)
    if root_input.is_symlink():
        return [_finding(root_input, "symlink")]
    root_path = Path(os.path.abspath(os.fspath(root_input)))
    if not root_path.is_dir():
        return [_finding(root_input, "blocked_unreadable")]

    findings: List[Finding] = []
    reviews, invalid_reviews = _reviews_for_root(root_path, reviewed_static_assets)
    if invalid_reviews:
        findings.append(_finding(STATIC_ASSET_REVIEW_MANIFEST, "blocked_review_manifest"))
    tracked_repo, tracked_paths = _tracked_info(root_path)
    pending = [root_path]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            findings.append(_finding(_relative(root_path, current) or ".", "blocked_unreadable"))
            continue

        for entry in entries:
            if entry.name == ".git":
                continue
            path = Path(entry.path)
            rel = _relative(root_path, path)
            try:
                if entry.is_symlink():
                    findings.append(_finding(rel, "symlink"))
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if entry.name == "__pycache__" and (
                        not tracked_repo
                        or (tracked_paths is not None and not _tracked_below(rel, tracked_paths))
                    ):
                        continue
                    if _forbidden_path(rel):
                        findings.append(_finding(rel, "forbidden_path"))
                    pending.append(path)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    findings.append(_finding(rel, "blocked_unreadable"))
                    continue
                if _untracked_generated(rel, tracked_repo, tracked_paths):
                    continue
                try:
                    data = path.read_bytes()
                except OSError:
                    findings.append(_finding(rel, "blocked_unreadable"))
                    continue
                findings.extend(_scan_bytes(rel, data, reviewed_static_assets=reviews))
            except OSError:
                findings.append(_finding(rel, "blocked_unreadable"))
    return _unique(findings)


def _git_command(root: Path, arguments: Sequence[str]) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(
            ["git", "-C", str(root)] + list(arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None


def _tracked_info(root: Path) -> Tuple[bool, Optional[Set[str]]]:
    """Return Git tracking knowledge for generated-directory handling."""

    top_level = _git_command(root, ["rev-parse", "--show-toplevel"])
    if top_level is None or top_level.returncode != 0:
        return False, None
    listing = _git_command(root, ["ls-files", "-z"])
    if listing is None or listing.returncode != 0:
        return True, None
    try:
        repository = Path(os.path.realpath(os.fsdecode(top_level.stdout.strip())))
        comparison_root = Path(os.path.realpath(os.fspath(root)))
    except (OSError, ValueError):
        return True, None
    tracked: Set[str] = set()
    for raw_path in listing.stdout.split(b"\0"):
        if not raw_path:
            continue
        candidate = repository / os.fsdecode(raw_path)
        try:
            tracked.add(candidate.relative_to(comparison_root).as_posix())
        except ValueError:
            continue
    return True, tracked


def _tracked_below(path: str, tracked: Set[str]) -> bool:
    prefix = path.rstrip("/") + "/"
    return path in tracked or any(item.startswith(prefix) for item in tracked)


def _untracked_generated(path: str, tracked_repo: bool, tracked_paths: Optional[Set[str]]) -> bool:
    if not tracked_repo or tracked_paths is None or path in tracked_paths:
        return False
    parts = path.split("/")
    return "__pycache__" in parts[:-1]


def scan_history(
    root: PathLike,
    reviewed_static_assets: Optional[ReviewInput] = None,
) -> List[Finding]:
    """Scan reachable blobs; manifest reviews require exact repository path and blob hash."""

    root_input = Path(root)
    if root_input.is_symlink():
        return [_finding(root_input, "symlink")]
    root_path = Path(os.path.abspath(os.fspath(root_input)))
    reviews, invalid_reviews = _reviews_for_root(root_path, reviewed_static_assets)
    findings: List[Finding] = []
    if invalid_reviews:
        findings.append(_finding(STATIC_ASSET_REVIEW_MANIFEST, "blocked_review_manifest"))
    listing = _git_command(root_path, ["rev-list", "--objects", "--all"])
    if listing is None or listing.returncode != 0:
        findings.append(_finding(".git", "blocked_history"))
        return _unique(findings)

    seen: Set[Tuple[str, str]] = set()
    for raw_line in listing.stdout.splitlines():
        fields = raw_line.split(None, 1)
        if len(fields) != 2:
            continue
        object_id = fields[0].decode("ascii", "ignore")
        if not re.fullmatch(r"[0-9a-fA-F]{4,64}", object_id):
            continue
        git_path = os.fsdecode(fields[1])
        key = (object_id.lower(), git_path)
        if key in seen:
            continue
        seen.add(key)
        object_type = _git_command(root_path, ["cat-file", "-t", object_id])
        if object_type is None or object_type.returncode != 0:
            findings.append(_finding("history/" + git_path, "blocked_history"))
            continue
        if object_type.stdout.strip() != b"blob":
            continue
        blob = _git_command(root_path, ["cat-file", "blob", object_id])
        if blob is None or blob.returncode != 0:
            findings.append(_finding("history/" + git_path, "blocked_history"))
            continue
        findings.extend(
            _scan_bytes(
                "history/" + git_path,
                blob.stdout,
                reviewed_static_assets=reviews,
                review_path=git_path,
            )
        )
    return _unique(findings)


def scan_repository(
    root: PathLike,
    reviewed_static_assets: Optional[ReviewInput] = None,
) -> List[Finding]:
    """Compatibility name for callers that describe the working scan fully."""

    return scan(root, reviewed_static_assets=reviewed_static_assets)


def _status(findings: Sequence[Finding]) -> Tuple[str, int]:
    if any(item["category"].startswith("blocked_") for item in findings):
        return "BLOCKED", 2
    if findings:
        return "REJECTED", 1
    return "PASS", 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit working files and optional reachable Git history")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--history", action="store_true", help="scan all reachable Git blobs")
    parser.add_argument(
        "--reviewed-static-asset",
        nargs=4,
        action="append",
        metavar=("PATH", "SHA256", "REVIEWER", "REASON"),
        help="manually reviewed image/font record; repeat per exact path and SHA256",
    )
    args = parser.parse_args(argv)

    reviewed_static_assets = [
        {"path": path, "sha256": digest, "reviewer": reviewer, "reason": reason}
        for path, digest, reviewer, reason in args.reviewed_static_asset or []
    ]

    findings = scan(args.root, reviewed_static_assets=reviewed_static_assets)
    if args.history:
        findings = _unique(findings + scan_history(args.root, reviewed_static_assets=reviewed_static_assets))
    status, exit_code = _status(findings)
    if status == "PASS" and _reviews_for_root(Path(args.root), reviewed_static_assets)[0]:
        status = "PASS_WITH_REVIEW"
    print("security_audit={}".format(status))
    for item in findings:
        print("finding category={} path={}".format(item["category"], item["path"]))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
