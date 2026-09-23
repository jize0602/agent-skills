#!/usr/bin/env python3
"""Build a deterministic manifest for the two public continuity Skills."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Dict, List


OFFICIAL_REPOSITORY = "jize0602/agent-skills"
SKILL_NAMES = ("github-bootstrap", "github-continuity")
TAG_PREFIX = "refs/tags/"
SEMVER_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\Z"
)


class ManifestError(ValueError):
    """Raised when the local source tree cannot produce a safe manifest."""


def _tag_version(ref: str) -> str:
    if not isinstance(ref, str) or not ref.startswith(TAG_PREFIX):
        raise ManifestError("ref must be a specific tag in refs/tags/<tag> form")
    tag = ref[len(TAG_PREFIX) :]
    invalid = not tag or tag.startswith("/") or tag.endswith(("/", "."))
    invalid = invalid or ".." in tag or "@{" in tag
    invalid = invalid or any(ord(char) < 32 or char in " ~^:?*[\\" for char in tag)
    components = tag.split("/")
    invalid = invalid or any(
        not part or part.startswith(".") or part.endswith(".lock") for part in components
    )
    if invalid:
        raise ManifestError("ref must contain a safe, concrete tag name")
    version = tag[1:] if tag.startswith("v") else tag
    if not SEMVER_RE.fullmatch(version):
        raise ManifestError("tag must carry a semantic version, for example v1.0.0")
    return version


def _skill_versions(repo_root: Path, release_version: str) -> Dict[str, str]:
    path = repo_root / "VERSION.json"
    if path.is_symlink() or not path.is_file():
        raise ManifestError("repo-root/VERSION.json must be a regular file")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("cannot read VERSION.json: {}".format(exc))
    if (
        not isinstance(document, dict)
        or type(document.get("schema_version")) is not int
        or document.get("schema_version") != 1
        or document.get("repository") != OFFICIAL_REPOSITORY
        or not isinstance(document.get("skills"), list)
    ):
        raise ManifestError("VERSION.json must declare schema_version 1 and the official repository")

    versions = {}
    for entry in document["skills"]:
        if not isinstance(entry, dict) or entry.get("name") not in SKILL_NAMES:
            continue
        name = entry["name"]
        version = entry.get("version")
        if name in versions:
            raise ManifestError("VERSION.json contains duplicate skill: {}".format(name))
        if not isinstance(version, str) or not SEMVER_RE.fullmatch(version):
            raise ManifestError("VERSION.json version for {} must be semantic version without v".format(name))
        if version != release_version:
            raise ManifestError(
                "VERSION.json version for {} must match tag version {}".format(name, release_version)
            )
        versions[name] = version
    missing = [name for name in SKILL_NAMES if name not in versions]
    if missing:
        raise ManifestError("VERSION.json is missing Skill versions: {}".format(", ".join(missing)))
    return versions


def _skill_roots(repo_root: Path) -> Dict[str, Path]:
    root = Path(repo_root)
    if root.is_symlink() or not root.is_dir():
        raise ManifestError("repo-root must be a real directory, not a symlink")

    skills_root = root / "skills"
    if skills_root.is_symlink() or not skills_root.is_dir():
        raise ManifestError("repo-root/skills must be a real directory")

    try:
        entries = list(os.scandir(str(skills_root)))
    except OSError as exc:
        raise ManifestError("cannot read repo-root/skills: {}".format(exc))
    names = {entry.name for entry in entries}
    if names != set(SKILL_NAMES):
        raise ManifestError("skills/ must contain exactly github-bootstrap and github-continuity")

    roots = {}
    for name in SKILL_NAMES:
        path = skills_root / name
        if path.is_symlink() or not path.is_dir():
            raise ManifestError("skills/{} must be a real directory".format(name))
        roots[name] = path
    return roots


def _files_for_skill(repo_root: Path, name: str, skill_root: Path) -> List[Path]:
    files = []
    for current, directories, filenames in os.walk(str(skill_root), followlinks=False):
        directories.sort()
        filenames.sort()
        current_path = Path(current)
        for directory in directories:
            child = current_path / directory
            mode = child.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ManifestError("symlink or non-directory in skills/{}".format(name))
        for filename in filenames:
            path = current_path / filename
            mode = path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ManifestError("symlink or non-regular file in skills/{}".format(name))
            files.append(path)

    relative_files = [path.relative_to(repo_root).as_posix() for path in files]
    if "skills/{}/SKILL.md".format(name) not in relative_files:
        raise ManifestError("skills/{}/SKILL.md is required".format(name))
    return files


def build_manifest(repo_root: Path, ref: str) -> dict:
    """Hash every regular file in the two allowed Skill trees."""
    release_version = _tag_version(ref)
    root = Path(repo_root).absolute()
    skill_roots = _skill_roots(root)
    versions = _skill_versions(root, release_version)
    skills = []
    for name in SKILL_NAMES:
        files = []
        for path in _files_for_skill(root, name, skill_roots[name]):
            relative = path.relative_to(root).as_posix()
            files.append(
                {
                    "path": relative,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                }
            )
        files.sort(key=lambda item: item["path"])
        skills.append({"name": name, "version": versions[name], "files": files})
    return {
        "schema_version": 1,
        "repository": OFFICIAL_REPOSITORY,
        "ref": ref,
        "skills": skills,
    }


def render_manifest(manifest: dict) -> str:
    return json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"


def _ensure_output_is_outside_skills(output: Path, skill_roots: Dict[str, Path]) -> None:
    if output.is_symlink():
        raise ManifestError("manifest output cannot be a symlink")
    resolved_output = output.resolve()
    for name, skill_root in skill_roots.items():
        try:
            resolved_output.relative_to(skill_root.resolve())
        except ValueError:
            continue
        raise ManifestError(
            "manifest output must be outside skills/{} to avoid self-reference".format(name)
        )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate a deterministic SHA-256 manifest for the public Skills. "
            "The manifest records declared origin and content hashes; it does not authenticate origin."
        )
    )
    parser.add_argument(
        "--repo-root",
        required=True,
        type=Path,
        help="local root of the downloaded agent-skills repository",
    )
    parser.add_argument(
        "--ref",
        required=True,
        help="specific semantic-version tag ref, for example refs/tags/v1.2.3",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSON to this new file (default: stdout); keep it outside skills/",
    )
    args = parser.parse_args(argv)

    try:
        manifest = build_manifest(args.repo_root, args.ref)
        output_text = render_manifest(manifest)
        if args.output is None:
            sys.stdout.write(output_text)
        else:
            roots = _skill_roots(Path(args.repo_root).absolute())
            _ensure_output_is_outside_skills(args.output, roots)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(output_text)
    except (ManifestError, OSError) as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
