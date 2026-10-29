#!/usr/bin/env python3
"""Install this workflow bundle into an existing project without overwriting it."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import tomllib


SKILL_NAME = "manager-dev-workflow"
START = "<!-- manager-dev-workflow:start -->"
END = "<!-- manager-dev-workflow:end -->"
EXCLUDED_DIRS = {"__pycache__", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}


class InstallError(ValueError):
    """An invalid bundle, unsafe path, or existing-file collision."""


@dataclass(frozen=True)
class Change:
    path: Path
    content: bytes
    previous: bytes | None = None


def safe_destination(path: Path, target: Path) -> None:
    """Reject redirected writes, including symlinked ancestors inside target."""
    try:
        relative = path.relative_to(target)
    except ValueError as exc:
        raise InstallError(f"Destination is outside the target: {path}") from exc
    cursor = target
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise InstallError(f"Symlink destination is not allowed: {cursor}")
        if cursor != path and cursor.exists() and not cursor.is_dir():
            raise InstallError(f"Destination parent is not a directory: {cursor}")
    if not path.resolve().is_relative_to(target):
        raise InstallError(f"Destination resolves outside the target: {path}")


def bundle_files(source: Path):
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_DIRS for part in relative.parts) or path.suffix in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise InstallError(f"Bundle symlinks are not supported: {relative}")
        if path.is_file():
            yield path, relative


def managed_block(source: Path) -> str:
    content = (source / "assets/AGENTS-section.md").read_bytes().decode("utf-8").strip()
    if START not in content and END not in content:
        return f"{START}\n{content}\n{END}"
    if content.count(START) != 1 or content.count(END) != 1:
        raise InstallError("AGENTS template has malformed managed-block markers")
    if not content.startswith(START) or not content.endswith(END):
        raise InstallError("AGENTS template must contain only its managed block")
    return content


def build_plan(source: Path, target: Path) -> list[Change]:
    source = source.resolve()
    target = target.resolve()
    if not target.is_dir():
        raise InstallError(f"Target must be an existing directory: {target}")
    destination = target / ".agents/skills" / SKILL_NAME
    if destination != source and (
        target.is_relative_to(source)
        or destination.is_relative_to(source)
        or source.is_relative_to(destination)
    ):
        raise InstallError("Recursive or overlapping bundle installation is not allowed")
    for required in ("SKILL.md", "assets/project.example.json", "assets/AGENTS-section.md"):
        if not (source / required).is_file():
            raise InstallError(f"Required bundle file is missing: {required}")
    roles = sorted((source / "assets/roles").glob("*.toml"))
    if not roles:
        raise InstallError("Bundle contains no role TOML files")
    files = list(bundle_files(source))  # Validate the complete source before planning writes.
    changes: dict[Path, Change] = {}

    def add(path: Path, content: bytes) -> None:
        safe_destination(path, target)
        if path.exists():
            if not path.is_file() or path.read_bytes() != content:
                raise InstallError(f"Existing file differs; no files changed: {path}")
            return
        if path in changes and changes[path].content != content:
            raise InstallError(f"Bundle maps conflicting content to {path}")
        changes[path] = Change(path, content)

    if destination != source:
        for path, relative in files:
            add(destination / relative, path.read_bytes())
    for role in roles:
        data = role.read_bytes()
        config = tomllib.loads(data.decode("utf-8"))
        if any(not isinstance(config.get(key), str) or not config[key].strip()
               for key in ("name", "description", "developer_instructions")):
            raise InstallError(f"Role lacks required name, description, or instructions: {role.name}")
        add(target / ".codex/agents" / role.name, data)

    profile = target / "docs/dev-workflow/project.json"
    safe_destination(profile, target)
    if profile.exists():
        if not profile.is_file():
            raise InstallError(f"Project profile is not a file: {profile}")
        # This belongs to the destination project, not to the bundle installer.
    else:
        add(profile, (source / "assets/project.example.json").read_bytes())

    agents = target / "AGENTS.md"
    safe_destination(agents, target)
    if agents.exists() and not agents.is_file():
        raise InstallError(f"AGENTS.md is not a file: {agents}")
    previous = agents.read_bytes() if agents.exists() else None
    current = previous.decode("utf-8") if previous is not None else ""
    block = managed_block(source)
    if START in current or END in current:
        if current.count(START) != 1 or current.count(END) != 1:
            raise InstallError("Existing AGENTS.md has malformed managed-block markers")
        match = re.search(re.escape(START) + r".*?" + re.escape(END), current, re.DOTALL)
        if not match or match.group() != block:
            raise InstallError("Existing AGENTS.md managed block differs; no files changed")
    else:
        separator = "" if not current else ("\n" if current.endswith("\n") else "\n\n")
        changes[agents] = Change(agents, (current + separator + block + "\n").encode("utf-8"), previous)
    return list(changes.values())


def apply_plan(changes: list[Change], target: Path) -> None:
    # Recheck every precondition before the first write in case files changed after planning.
    for change in changes:
        safe_destination(change.path, target)
        if change.previous is None:
            if change.path.exists():
                raise InstallError(f"Destination appeared after preflight: {change.path}")
        elif not change.path.is_file() or change.path.read_bytes() != change.previous:
            raise InstallError(f"Destination changed after preflight: {change.path}")
    for change in changes:
        safe_destination(change.path, target)
        change.path.parent.mkdir(parents=True, exist_ok=True)
        if change.previous is None:
            with change.path.open("xb") as stream:
                stream.write(change.content)
        else:
            change.path.write_bytes(change.content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, type=Path, help="An existing project directory")
    parser.add_argument("--dry-run", action="store_true", help="Validate and show changes without writing")
    args = parser.parse_args(argv)
    target = args.target.expanduser().resolve()
    try:
        changes = build_plan(Path(__file__).resolve().parents[1], target)
        if not args.dry_run:
            apply_plan(changes, target)
    except (InstallError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(f"Installation failed: {exc}", file=sys.stderr)
        return 1
    label = "Would write" if args.dry_run else "Wrote"
    for change in changes:
        print(f"{label}: {change.path.relative_to(target)}")
    print(f"{'Dry run' if args.dry_run else 'Installation'} complete: {len(changes)} file(s); project profile preserved if present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
