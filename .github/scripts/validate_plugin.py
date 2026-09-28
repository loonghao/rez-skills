#!/usr/bin/env python3
"""Validate the rez-skills agent plugin layout.

This repository is an agent plugin: its root is the plugin root, and
``skills/<name>/SKILL.md`` directories are the plugin's skill components. The
same ``skills/`` directory is what ``.github/workflows/sync-skills.yml``
discovers and publishes to ClawHub, so the checks below guard both surfaces at
once:

* the plugin manifest and marketplace manifest follow the agent plugin spec;
* every directory that the ClawHub workflow would publish is a valid skill.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
PLUGIN_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_MANIFEST = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# Plugin `name` must be kebab-case, with no spaces, "@", ":" or path separators.
KEBAB_CASE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
NAME_FIELD = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
DESCRIPTION_FIELD = re.compile(r"^description:\s*(.+?)\s*$", re.MULTILINE)


class Validator:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load_json(path: Path, validator: Validator) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        validator.error(f"missing manifest: {path.relative_to(REPO_ROOT)}")
        return None
    except json.JSONDecodeError as exc:
        validator.error(f"{path.relative_to(REPO_ROOT)} is not valid JSON: {exc}")
        return None

    if not isinstance(data, dict):
        validator.error(f"{path.relative_to(REPO_ROOT)} must contain a JSON object")
        return None
    return data


def validate_plugin_manifest(validator: Validator) -> None:
    manifest = load_json(PLUGIN_MANIFEST, validator)
    if manifest is None:
        return

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        validator.error("plugin.json: 'name' is required and must be a non-empty string")
    elif not KEBAB_CASE.match(name):
        validator.error(f"plugin.json: 'name' must be kebab-case, got {name!r}")

    for field in ("version", "description"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            validator.warn(f"plugin.json: '{field}' is not set")

    author = manifest.get("author")
    if not isinstance(author, dict) or not author.get("name"):
        validator.warn("plugin.json: 'author.name' is not set")

    # A component path declared in the manifest must exist inside the plugin root.
    for field in ("skills", "commands", "agents"):
        if field not in manifest:
            continue
        value = manifest[field]
        paths = value if isinstance(value, list) else [value]
        for path in paths:
            if not isinstance(path, str):
                validator.error(f"plugin.json: '{field}' entries must be strings")
                continue
            if not (REPO_ROOT / path.lstrip("./")).exists():
                validator.error(f"plugin.json: '{field}' points at a missing path: {path}")


def validate_marketplace_manifest(validator: Validator, skill_dirs: list[str]) -> None:
    manifest = load_json(MARKETPLACE_MANIFEST, validator)
    if manifest is None:
        return

    name = manifest.get("name")
    if not isinstance(name, str) or not name:
        validator.error("marketplace.json: 'name' is required and must be a non-empty string")
    elif not KEBAB_CASE.match(name):
        validator.error(f"marketplace.json: 'name' must be kebab-case, got {name!r}")

    owner = manifest.get("owner")
    if not isinstance(owner, dict) or not owner.get("name"):
        validator.error("marketplace.json: 'owner.name' is required")

    plugins = manifest.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        validator.error("marketplace.json: 'plugins' must be a non-empty array")
        return

    for entry in plugins:
        if not isinstance(entry, dict):
            validator.error("marketplace.json: each plugin entry must be an object")
            continue
        entry_name = entry.get("name")
        if not isinstance(entry_name, str) or not entry_name:
            validator.error("marketplace.json: each plugin entry needs a 'name'")
        source = entry.get("source")
        if not isinstance(source, str) or not source:
            validator.error(f"marketplace.json: plugin {entry_name!r} needs a 'source'")
            continue
        if ".." in Path(source).parts:
            validator.error(f"marketplace.json: source must not escape the root: {source}")
            continue
        if not (REPO_ROOT / source).is_dir():
            validator.error(f"marketplace.json: source does not exist: {source}")

    # The workflow publishes every directory under skills/, so the marketplace must expose a
    # plugin whose source covers each one. Otherwise skills ship to ClawHub but not to anyone
    # who installs the plugin.
    sources = [
        (REPO_ROOT / entry["source"]).resolve()
        for entry in plugins
        if isinstance(entry, dict) and isinstance(entry.get("source"), str)
    ]
    for skill in skill_dirs:
        skill_path = (SKILLS_DIR / skill).resolve()
        if not any(skill_path.is_relative_to(source) for source in sources):
            validator.error(
                f"skills/{skill} is not covered by any marketplace plugin source, "
                f"so it would publish to ClawHub but not install with the plugin"
            )

    print(f"marketplace.json lists {len(plugins)} plugin(s), covering {len(skill_dirs)} skill(s)")


def validate_skills(validator: Validator) -> list[str]:
    """Check every directory the ClawHub workflow would discover and publish.

    Returns the skill directory names that were checked.
    """
    if not SKILLS_DIR.is_dir():
        validator.error("the skills/ directory does not exist")
        return []

    dirs = sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir())
    if not dirs:
        validator.error("no skill directories found under skills/")
        return []

    for skill_dir in dirs:
        skill_md = skill_dir / "SKILL.md"
        rel = skill_dir.relative_to(REPO_ROOT)
        if not skill_md.is_file():
            validator.error(f"{rel}: missing SKILL.md")
            continue

        try:
            content = skill_md.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            validator.error(f"{rel}/SKILL.md is not valid UTF-8: {exc}")
            continue

        match = FRONTMATTER.match(content)
        if not match:
            validator.error(f"{rel}/SKILL.md is missing YAML frontmatter delimited by '---'")
            continue

        frontmatter = match.group(1)
        name_match = NAME_FIELD.search(frontmatter)
        if not name_match:
            validator.error(f"{rel}/SKILL.md frontmatter is missing 'name'")
        else:
            name = name_match.group(1).strip().strip("\"'")
            if name != skill_dir.name:
                validator.error(
                    f"{rel}/SKILL.md frontmatter 'name' is {name!r}, "
                    f"expected {skill_dir.name!r} to match the directory"
                )

        description_match = DESCRIPTION_FIELD.search(frontmatter)
        if not description_match or not description_match.group(1).strip().strip("\"'"):
            validator.error(f"{rel}/SKILL.md frontmatter is missing 'description'")

    return [d.name for d in dirs]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    args = parser.parse_args()

    validator = Validator()
    skill_dirs = validate_skills(validator)
    validate_plugin_manifest(validator)
    validate_marketplace_manifest(validator, skill_dirs)

    for warning in validator.warnings:
        print(f"::warning::{warning}")
    for error in validator.errors:
        print(f"::error::{error}")

    status = "failed" if validator.errors else "passed"
    suffix = " with warnings" if validator.warnings and not validator.errors else ""
    print(f"Plugin validation {status}{suffix}: {len(skill_dirs)} skill(s) checked")

    if validator.errors:
        return 1
    if args.strict and validator.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
