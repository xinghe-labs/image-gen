#!/usr/bin/env python3
"""Install the image-gen skill into an agent-host skill directory.

This is a human-facing helper for machines without Node (the standard route
is ``npx skills add xinghe-labs/image-gen``). Standard library
only; run it from a clone or an extracted release archive of this
repository.

    python install.py [--root DIR] [--all] [--force]

With no arguments it installs into the first detected skill root
(``~/.agents/skills``, ``~/.codex/skills``, or ``~/.claude/skills``), creating
``~/.agents/skills`` when none exist. ``--all`` installs into every detected
root. The installer refuses to replace a foreign directory unless ``--force``
is given, and it prints plain text — it is not part of the JSON CLI contract.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SKILL_NAME = "image-gen"

KNOWN_SKILL_ROOTS = [
    Path.home() / ".agents" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".claude" / "skills",
]

SKIP_DIRECTORIES = {".git", "__pycache__", ".pytest_cache"}


class InstallError(Exception):
    """A known, user-facing installation problem (exit code 2)."""


def configure_utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


def declared_skill_name(skill_dir: Path) -> str | None:
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        return None
    try:
        for line in skill_md.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("name:"):
                return stripped.split(":", 1)[1].strip().strip("'\"")
            if stripped == "---" and line.startswith("---"):
                continue
    except OSError:
        return None
    return None


def is_same_skill(dest: Path) -> bool:
    """Whether an existing directory is a previous install of this skill."""
    if not dest.exists():
        return True
    return declared_skill_name(dest) == SKILL_NAME


def copy_skill_source(source_root: Path, staging: Path) -> int:
    copied = 0
    for path in source_root.rglob("*"):
        relative = path.relative_to(source_root)
        if any(part in SKIP_DIRECTORIES for part in relative.parts):
            continue
        if path.is_file() and path.suffix == ".pyc":
            continue
        target = staging / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied += 1
    return copied


def install_into(source_root: Path, target_root: Path, force: bool) -> None:
    dest = target_root / SKILL_NAME
    if dest.exists() and not is_same_skill(dest):
        if not force:
            raise InstallError(
                f"refusing to replace a foreign directory: {dest}\n"
                "  pass --force to replace it anyway"
            )
        print(f"  --force: replacing foreign directory {dest}")
    target_root.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    staging = target_root / f".{SKILL_NAME}.installing"
    if staging.exists():
        shutil.rmtree(staging)
    try:
        copied = copy_skill_source(source_root, staging)
        staging.rename(dest)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    print(f"  installed {copied} files -> {dest}")


def main() -> int:
    configure_utf8_streams()
    parser = argparse.ArgumentParser(description="Install the image-gen skill.")
    parser.add_argument("--root", help="Install into this directory instead of the detected skill roots.")
    parser.add_argument("--all", action="store_true", help="Install into every detected skill root.")
    parser.add_argument("--force", action="store_true", help="Replace an existing directory even if it is not this skill.")
    args = parser.parse_args()

    source_root = Path(__file__).resolve().parent
    if not (source_root / "SKILL.md").is_file():
        raise InstallError(f"SKILL.md not found next to install.py: {source_root}")

    if args.root:
        targets = [Path(args.root).expanduser()]
    else:
        detected = [root for root in KNOWN_SKILL_ROOTS if root.is_dir()]
        if not detected:
            targets = [KNOWN_SKILL_ROOTS[0]]
        elif args.all:
            targets = detected
        else:
            targets = [detected[0]]

    print(f"Installing {SKILL_NAME} from {source_root}")
    for target in targets:
        install_into(source_root, target, args.force)
    print("Done. The agent discovers the skill on its next session start.")

    config_probe = {
        "api_key_set": bool(
            __import__("os").environ.get("IMAGE_GENERATION_API_KEY")
            or __import__("os").environ.get("GPT_IMAGE_API_KEY")
        ),
        "base_url_set": bool(
            __import__("os").environ.get("IMAGE_GENERATION_BASE_URL")
            or __import__("os").environ.get("GPT_IMAGE_BASE_URL")
        ),
    }
    if not (config_probe["api_key_set"] and config_probe["base_url_set"]):
        print(
            "Gateway not detected in this shell's environment. Set:\n"
            "  IMAGE_GENERATION_API_KEY=<your third-party key>\n"
            "  IMAGE_GENERATION_BASE_URL=https://your-provider.example/v1"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except InstallError as exc:
        print(f"install: {exc}", file=sys.stderr)
        sys.exit(2)
