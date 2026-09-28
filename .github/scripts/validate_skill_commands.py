#!/usr/bin/env python3
"""Smoke-test the rez commands that the skills tell an agent to run.

Every shell snippet in ``skills/*/SKILL.md`` is a copy-paste contract: an agent
-- or a human -- will run those lines verbatim. Nothing in CI used to parse
them, so a wrong command name or a flag that rez never had shipped green. The
canonical example is ``skills/rez-package-authoring/SKILL.md``, which told
readers to run ``rez-env --tools``; ``--tools`` is defined in
``src/rez/cli/context.py``, not in ``src/rez/cli/env.py``.

This script closes that gap:

1. Extract every rez invocation from the fenced shell blocks *and* from the
   inline `` `code` `` spans of every ``skills/*/SKILL.md``. Inline spans
   matter: the ``rez-env --tools`` mistake lived in prose, not in a fence.
2. Build the real CLI inventory from the *installed* rez package, using
   ``rez.cli._main.setup_parser()`` plus every lazily attached sub-parser. That
   includes options rez registers with ``help=argparse.SUPPRESS`` -- namely the
   ``-v/--verbose``, ``--debug`` and ``--profile`` flags that
   ``rez.cli._main._add_common_args()`` injects into every sub-parser. Those
   flags are invisible to ``rez-env --help``, so a ``--help``-only scan would
   report false positives on them.
3. Fail on unknown subcommands and unknown flags, with a pointer to the
   subcommand that does own the flag when there is one.
4. Always execute ``rez-<cmd> --help`` for the baseline subcommands in
   :data:`REQUIRED_SUBCOMMANDS`, plus every documented command that ships a
   console script, so the lane still proves something even if the docs stop
   mentioning a command.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# Fenced blocks that hold real shell commands. Other languages (python, yaml,
# json, text) are illustrations of file contents, not commands to run.
SHELL_FENCE_LANGS = {"", "bash", "sh", "shell", "console", "zsh", "ksh", "fish"}

# The subcommands this lane must exercise on every run, whether or not the
# skill docs happen to mention them.
REQUIRED_SUBCOMMANDS = ("context", "search", "env", "depends", "build", "release", "test")

# `rez` itself, or any `rez-<subcommand>` console script.
COMMAND_RE = re.compile(r"^rez(?:-[a-z0-9][a-z0-9-]*)?$")
FENCE_RE = re.compile(r"^(?P<fence>`{3,}|~{3,})\s*(?P<info>[^`]*)$")
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
PROMPT_RE = re.compile(r"^\s*[$>]\s+")
SHELL_OPERATORS = {"|", "||", "&&", ";", "&", "|&"}


@dataclass(frozen=True)
class Site:
    """A `path:line` reference into a skill document."""

    path: Path
    line: int

    def format(self) -> str:
        return f"{self.path.as_posix()}:{self.line}"


@dataclass
class RezCli:
    """The option inventory of the installed rez CLI."""

    version: str
    location: str
    top_options: set[str]
    subcommands: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> "RezCli":
        import rez
        from rez.cli._main import setup_parser

        parser = setup_parser()
        # Sub-parsers are attached lazily; force them all so their options
        # (including the argparse.SUPPRESS ones) are visible.
        parser._setup_all_subparsers()

        top_options = {opt for action in parser._actions for opt in action.option_strings}
        subcommands: dict[str, set[str]] = {}
        for action in parser._subparsers._actions:
            name_map = getattr(action, "_name_parser_map", None)
            if not name_map:
                continue
            for name, sub_parser in name_map.items():
                subcommands[name] = {
                    opt for act in sub_parser._actions for opt in act.option_strings
                }

        return cls(
            version=rez.__version__,
            location=str(Path(rez.__file__).parent),
            top_options=top_options,
            subcommands=subcommands,
        )

    def options_for(self, command: str) -> set[str]:
        return self.top_options if command == "rez" else self.subcommands.get(command, set())

    def flag_owner_names(self, flag: str) -> list[str]:
        """Subcommands that define `flag`, used for "did you mean" hints."""
        owners = [name for name, opts in self.subcommands.items() if flag in opts]
        if flag in self.top_options:
            owners.append("rez")
        return sorted(owners)


@dataclass
class Findings:
    commands: dict[str, list[Site]] = field(default_factory=dict)
    flags: dict[tuple[str, str], list[Site]] = field(default_factory=dict)
    skipped_lines: list[tuple[Site, str]] = field(default_factory=list)

    def add_command(self, command: str, site: Site) -> None:
        self.commands.setdefault(command, []).append(site)

    def add_flag(self, command: str, flag: str, site: Site) -> None:
        self.flags.setdefault((command, flag), []).append(site)

    @property
    def command_count(self) -> int:
        return sum(len(sites) for sites in self.commands.values())

    @property
    def flag_count(self) -> int:
        return sum(len(sites) for sites in self.flags.values())


def iter_shell_lines(text: str):
    """Yield ``(lineno, origin, line)`` for every line that can hold a command.

    ``origin`` is ``prose`` outside fenced blocks and ``fence:<lang>`` inside a
    fenced block whose info string names a shell.
    """
    fence: str | None = None
    lang = ""
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = FENCE_RE.match(line.strip())
        if fence is None:
            if match:
                fence = match.group("fence")[0] * 3
                lang = match.group("info").strip().lower()
                continue
            yield lineno, "prose", line
        elif match and match.group("fence")[0] * 3 == fence:
            fence = None
            continue
        elif lang in SHELL_FENCE_LANGS:
            yield lineno, f"fence:{lang or 'plain'}", line


def tokenize(line: str) -> list[str] | None:
    """Split a shell line into tokens, dropping comments and prompts.

    Returns ``None`` when the line cannot be parsed (unbalanced quotes), which
    keeps a malformed snippet from failing the whole lane.
    """
    stripped = PROMPT_RE.sub("", line).strip()
    if not stripped or stripped.startswith("#"):
        return []
    try:
        # comments=True is quote-aware, so `#` inside a quoted request stays.
        return shlex.split(stripped, comments=True, posix=True)
    except ValueError:
        return None


def collect_from_tokens(
    tokens: list[str], cli: RezCli, site: Site, findings: Findings, strict: bool = True
) -> None:
    """Walk one shell line (or one inline code span) and record what it uses.

    `strict` is False for a bare inline span such as ``` `rez-cli` ```: in prose a
    single unknown `rez-<name>` token is usually a cross-reference to another
    skill or to a Rez docs page, so it is left alone. A span that also names a
    flag (`rez-env --tools`) is always checked, and fenced shell blocks are
    always strict.
    """
    current: str | None = None
    for token in tokens:
        # `--` ends option parsing; what follows is the command to run.
        if token == "--":
            current = None
            continue
        if token in SHELL_OPERATORS:
            current = None
            continue
        if COMMAND_RE.match(token):
            current = "rez" if token == "rez" else token[len("rez-") :]
            known = current == "rez" or current in cli.subcommands
            if strict or known:
                findings.add_command(current, site)
            continue
        if current is None:
            continue
        # `rez env ...` names the subcommand as a positional argument.
        if current == "rez" and token in cli.subcommands:
            current = token
            findings.add_command(current, site)
            continue
        if token.startswith("-") and len(token) > 1:
            # Skip documentation shorthand such as `-t/--time`: it is two
            # option strings joined for brevity, not something to run.
            if "/" in token:
                continue
            findings.add_flag(current, token, site)


def collect(skill_md: Path, cli: RezCli, findings: Findings) -> None:
    rel = skill_md.relative_to(REPO_ROOT)
    text = skill_md.read_text(encoding="utf-8")

    for lineno, origin, line in iter_shell_lines(text):
        site = Site(rel, lineno)
        if origin == "prose":
            # Only spans that start with a rez command can be attributed; a
            # bare `--tools` in prose has no owner to check against.
            for span in INLINE_CODE_RE.findall(line):
                tokens = tokenize(span)
                if tokens is None:
                    findings.skipped_lines.append((site, span))
                    continue
                if tokens and COMMAND_RE.match(tokens[0]):
                    collect_from_tokens(tokens, cli, site, findings, strict=len(tokens) > 1)
            continue

        tokens = tokenize(line)
        if tokens is None:
            findings.skipped_lines.append((site, line.strip()))
            continue
        collect_from_tokens(tokens, cli, site, findings)


def split_flag(token: str) -> str:
    """Reduce a used flag to the option string rez registers."""
    option = token.split("=", 1)[0]
    return option


def expand_short_cluster(option: str) -> list[str]:
    """Expand a combined short-flag cluster such as `-vv` into `['-v', '-v']`."""
    if option.startswith("--") or len(option) <= 2:
        return [option]
    return [f"-{char}" for char in option[1:]]


def flag_is_known(cli: RezCli, command: str, token: str) -> bool:
    known = cli.options_for(command)
    option = split_flag(token)
    if option in known:
        return True
    return all(part in known for part in expand_short_cluster(option))


def describe_sites(sites: list[Site], limit: int = 3) -> str:
    shown = ", ".join(site.format() for site in sites[:limit])
    extra = len(sites) - limit
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def validate(findings: Findings, cli: RezCli) -> list[str]:
    errors: list[str] = []

    for command, sites in sorted(findings.commands.items()):
        if command != "rez" and command not in cli.subcommands:
            hint = difflib.get_close_matches(command, cli.subcommands, n=1)
            suffix = f"; did you mean 'rez-{hint[0]}'?" if hint else ""
            errors.append(
                f"{sites[0].path.as_posix()}:{sites[0].line}: "
                f"'rez-{command}' is not a rez subcommand (rez {cli.version}){suffix}"
            )

    for (command, token), sites in sorted(findings.flags.items()):
        if flag_is_known(cli, command, token):
            continue
        owners = cli.flag_owner_names(split_flag(token))
        suffix = ""
        if owners:
            shown = ", ".join(f"rez-{name}" if name != "rez" else name for name in owners[:3])
            suffix = f"; it is defined on: {shown}"
        label = "rez" if command == "rez" else f"rez-{command}"
        errors.append(
            f"{sites[0].path.as_posix()}:{sites[0].line}: "
            f"{label} has no '{split_flag(token)}' option (rez {cli.version}){suffix} "
            f"[{describe_sites(sites)}]"
        )

    return errors


def smoke_run(cli: RezCli, commands: list[str]) -> list[str]:
    """Run `rez-<cmd> --help` for every command that ships a console script."""
    errors: list[str] = []
    for command in sorted(commands):
        executable = shutil.which(f"rez-{command}")
        if executable is None:
            if command in REQUIRED_SUBCOMMANDS:
                errors.append(f"rez-{command} is not installed on PATH (rez {cli.version})")
            continue
        proc = subprocess.run(
            [executable, "--help"],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            output = (proc.stderr or proc.stdout).strip().splitlines()
            errors.append(
                f"`rez-{command} --help` exited {proc.returncode}: "
                f"{output[-1] if output else 'no output'}"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump",
        action="store_true",
        help="print every extracted command and flag usage, then exit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    args = parser.parse_args()

    try:
        cli = RezCli.load()
    except ImportError as exc:  # pragma: no cover - environment problem only
        print(f"::error::rez is not importable, install it before running this check: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"::error::could not inspect the rez CLI: {type(exc).__name__}: {exc}")
        return 1

    if not SKILLS_DIR.is_dir():
        print("::error::the skills/ directory does not exist")
        return 1

    findings = Findings()
    documents = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not documents:
        print("::error::no skills/*/SKILL.md files found")
        return 1

    for skill_md in documents:
        collect(skill_md, cli, findings)

    if args.dump:
        for command, sites in sorted(findings.commands.items()):
            label = "rez" if command == "rez" else f"rez-{command}"
            print(f"{label}\t{len(sites)}\t{sites[0].format()}")
        for (command, token), sites in sorted(findings.flags.items()):
            label = "rez" if command == "rez" else f"rez-{command}"
            print(f"{label} {split_flag(token)}\t{len(sites)}\t{sites[0].format()}")
        return 0

    errors = validate(findings, cli)

    documented = sorted(
        {command for command in findings.commands if command != "rez"}
        | set(REQUIRED_SUBCOMMANDS)
    )
    errors.extend(smoke_run(cli, documented))

    warnings: list[str] = []

    # A lane that checks nothing is worse than no lane: it reports success while
    # the docs rot, which is how `rez-env --tools` reached a merged release.
    if not findings.command_count:
        errors.append(
            "no rez commands found in skills/*/SKILL.md -- the extractor is not "
            "reading the documents, so this lane proves nothing"
        )

    for command in REQUIRED_SUBCOMMANDS:
        if command not in findings.commands:
            warnings.append(
                f"rez-{command} is never mentioned in the skill docs, so only the "
                f"baseline --help smoke test covers it"
            )

    for site, text in findings.skipped_lines:
        warnings.append(f"{site.format()}: could not parse shell snippet, skipped: {text}")

    for warning in warnings:
        print(f"::warning::{warning}")
    for error in errors:
        print(f"::error::{error}")

    if not errors and args.strict and warnings:
        print(
            f"Skill command validation failed with {len(warnings)} warning(s) under --strict: "
            f"{findings.command_count} command usage(s) and {findings.flag_count} flag usage(s) "
            f"in {len(documents)} SKILL.md file(s)"
        )
        return 1

    if errors:
        print(
            f"Skill command validation failed with {len(errors)} error(s): "
            f"{findings.command_count} command usage(s) and {findings.flag_count} flag usage(s) "
            f"in {len(documents)} SKILL.md file(s), against rez {cli.version} "
            f"({len(cli.subcommands)} subcommands)"
        )
        return 1

    print(
        f"Skill command validation passed: {findings.command_count} command usage(s) and "
        f"{findings.flag_count} flag usage(s) in {len(documents)} SKILL.md file(s), "
        f"against rez {cli.version} ({len(cli.subcommands)} subcommands, "
        f"{len(documented)} subcommand(s) smoke-run) from {cli.location}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
