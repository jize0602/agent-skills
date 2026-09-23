#!/usr/bin/env python3
"""Verify local public Skill hashes and copy files without executing them."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from pathlib import Path, PurePosixPath


OFFICIAL_REPOSITORY = "jize0602/agent-skills"
SKILL_NAMES = ("github-bootstrap", "github-continuity")
TAG_PREFIX = "refs/tags/"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


class InstallError(ValueError):
    """Raised when a downloaded repository or manifest fails verification."""


def _tag_version(ref):
    if not isinstance(ref, str) or not ref.startswith(TAG_PREFIX):
        raise InstallError("ref must be a specific tag in refs/tags/<tag> form")
    tag = ref[len(TAG_PREFIX) :]
    invalid = not tag or tag.startswith("/") or tag.endswith(("/", "."))
    invalid = invalid or ".." in tag or "@{" in tag
    invalid = invalid or any(ord(char) < 32 or char in " ~^:?*[\\" for char in tag)
    components = tag.split("/")
    invalid = invalid or any(
        not part or part.startswith(".") or part.endswith(".lock") for part in components
    )
    if invalid:
        raise InstallError("ref must contain a safe, concrete tag name")
    version = tag[1:] if tag.startswith("v") else tag
    if not SEMVER_RE.fullmatch(version):
        raise InstallError("tag must carry a semantic version, for example v1.0.0")
    return version


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("manifest contains duplicate JSON key: {}".format(key))
        result[key] = value
    return result


def _safe_manifest_path(value, skill_name):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise InstallError("file path must be a safe repository-relative POSIX path")
    parts = value.split("/")
    if (
        value.startswith("/")
        or any(part in ("", ".", "..") for part in parts)
        or PurePosixPath(value).as_posix() != value
        or len(parts) < 3
        or parts[:2] != ["skills", skill_name]
    ):
        raise InstallError("file path must be a safe path inside skills/{}".format(skill_name))
    return parts


def _validate_manifest(manifest, expected_ref):
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version",
        "repository",
        "ref",
        "skills",
    }:
        raise InstallError("manifest must contain exactly schema_version, repository, ref, skills")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise InstallError("schema_version must be 1")
    if manifest["repository"] != OFFICIAL_REPOSITORY:
        raise InstallError("repository declaration must be {}".format(OFFICIAL_REPOSITORY))
    version = _tag_version(manifest["ref"])
    _tag_version(expected_ref)
    if manifest["ref"] != expected_ref:
        raise InstallError("manifest ref does not match --expected-ref")
    skills = manifest["skills"]
    if not isinstance(skills, list) or [
        item.get("name") if isinstance(item, dict) else None for item in skills
    ] != list(SKILL_NAMES):
        raise InstallError("skills must list github-bootstrap and github-continuity in order")

    validated = []
    for name, skill in zip(SKILL_NAMES, skills):
        if not isinstance(skill, dict) or set(skill) != {"name", "version", "files"}:
            raise InstallError("each skill must contain exactly name, version, and files")
        if skill["version"] != version:
            raise InstallError("skill version must match the tag in ref")
        files = skill["files"]
        if not isinstance(files, list) or not files:
            raise InstallError("files must be a non-empty list for {}".format(name))
        clean_files = []
        seen_paths = set()
        for item in files:
            if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                raise InstallError("each file entry must contain exactly path and sha256")
            parts = _safe_manifest_path(item["path"], name)
            if item["path"] in seen_paths:
                raise InstallError("duplicate manifest file path: {}".format(item["path"]))
            seen_paths.add(item["path"])
            if not isinstance(item["sha256"], str) or not SHA256_RE.fullmatch(item["sha256"]):
                raise InstallError("sha256 must be 64 lowercase hexadecimal characters")
            clean_files.append({"path": item["path"], "parts": parts, "sha256": item["sha256"]})
        if [item["path"] for item in clean_files] != sorted(seen_paths):
            raise InstallError("files must be sorted by path for {}".format(name))
        if "skills/{}/SKILL.md".format(name) not in seen_paths:
            raise InstallError("skills/{}/SKILL.md is required".format(name))
        validated.append({"name": name, "files": clean_files})
    return validated


def _skill_roots(repo_root):
    root = Path(repo_root)
    if root.is_symlink() or not root.is_dir():
        raise InstallError("repo-root must be a real directory, not a symlink")
    skills_root = root / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise InstallError("repo-root/skills must be a real directory")
    try:
        entries = list(os.scandir(str(skills_root)))
    except OSError as exc:
        raise InstallError("cannot read repo-root/skills: {}".format(exc))
    if {entry.name for entry in entries} != set(SKILL_NAMES):
        raise InstallError("skills/ must contain exactly github-bootstrap and github-continuity")
    roots = {}
    for name in SKILL_NAMES:
        path = skills_root / name
        if path.is_symlink() or not path.is_dir():
            raise InstallError("skills/{} must be a real directory".format(name))
        roots[name] = path
    return roots


def _actual_files(name, skill_root):
    files = set()
    for current, directories, filenames in os.walk(str(skill_root), followlinks=False):
        directories.sort()
        filenames.sort()
        current_path = Path(current)
        for directory in directories:
            child = current_path / directory
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise InstallError("symlink or non-directory in skills/{}".format(name))
        for filename in filenames:
            path = current_path / filename
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise InstallError("symlink or non-regular file in skills/{}".format(name))
            files.add(path.relative_to(skill_root.parent.parent).as_posix())
    if "skills/{}/SKILL.md".format(name) not in files:
        raise InstallError("skills/{}/SKILL.md is required".format(name))
    return files


def _read_manifest(manifest_path):
    path = Path(manifest_path)
    if path.is_symlink() or not path.is_file():
        raise InstallError("manifest must be a regular local file, not a symlink")
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream, object_pairs_hook=_unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InstallError("cannot read manifest JSON: {}".format(exc))


def install_bundle(repo_root, manifest_path, dest, expected_ref):
    """Verify local hashes and copy files; caller must authenticate repo/tag provenance first.

    The repository field in a manifest is a declaration, not identity proof. The calling
    Agent/platform must fetch from the official repository, reject forks, and verify that
    expected_ref resolves to the intended official commit before calling this function.
    """
    root = Path(repo_root)
    manifest = _read_manifest(manifest_path)
    skills = _validate_manifest(manifest, expected_ref)
    roots = _skill_roots(root)

    verified_files = {}
    for skill in skills:
        name = skill["name"]
        actual = _actual_files(name, roots[name])
        listed = {item["path"] for item in skill["files"]}
        if listed != actual:
            extra = sorted(actual - listed)
            missing = sorted(listed - actual)
            raise InstallError(
                "manifest file set mismatch for {} (unlisted={}, missing={})".format(
                    name, extra, missing
                )
            )
        for item in skill["files"]:
            source = root.joinpath(*item["parts"])
            if source.is_symlink() or not source.is_file():
                raise InstallError("manifest source is not a regular file: {}".format(item["path"]))
            contents = source.read_bytes()
            digest = hashlib.sha256(contents).hexdigest()
            if digest != item["sha256"]:
                raise InstallError("sha256 mismatch: {}".format(item["path"]))
            verified_files[item["path"]] = contents

    destination = Path(dest).expanduser()
    if destination.exists() and (destination.is_symlink() or not destination.is_dir()):
        raise InstallError("dest must be a directory when it already exists")
    targets = [destination / name for name in SKILL_NAMES]
    for target in targets:
        if os.path.lexists(str(target)):
            raise InstallError("installation destination already exists: {}".format(target))

    destination.mkdir(parents=True, exist_ok=True)
    created_targets = []
    try:
        for name, target in zip(SKILL_NAMES, targets):
            target.mkdir()
            created_targets.append(target)
            prefix = "skills/{}/".format(name)
            for path, contents in verified_files.items():
                if not path.startswith(prefix):
                    continue
                relative = Path(*path.split("/")[2:])
                output = target / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("xb") as stream:
                    stream.write(contents)
    except OSError as exc:
        for target in reversed(created_targets):
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(str(target))
        raise InstallError("installation failed without overwriting existing files: {}".format(exc))
    return targets


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Check a local Skill bundle's declared origin and file hashes, then install it "
            "without running its contents. This does not authenticate repository identity."
        ),
        epilog=(
            "PROVENANCE PRECONDITION: Before invoking, the calling Agent/platform must fetch "
            "the source from https://github.com/jize0602/agent-skills, independently verify "
            "that the repository identity is jize0602/agent-skills (not a fork) and that the "
            "requested tag resolves to the intended commit. A manifest's repository field "
            "and local hashes do not prove origin or authenticate a publisher."
        ),
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="local root of the already-downloaded repository (network discovery is external)",
    )
    parser.add_argument("--manifest", required=True, type=Path, help="local release manifest JSON")
    parser.add_argument(
        "--expected-ref",
        required=True,
        help="exact tag ref independently verified by the calling Agent, e.g. refs/tags/v1.0.0",
    )
    parser.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="existing or new parent directory; existing Skill directories are never overwritten",
    )
    args = parser.parse_args(argv)
    try:
        targets = install_bundle(args.repo_root, args.manifest, args.dest, args.expected_ref)
    except (InstallError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    for target in targets:
        print("installed {}".format(target.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
