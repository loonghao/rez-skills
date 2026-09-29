#!/usr/bin/env python3
"""Install the rez-skills bundle into an OpenAI Codex CLI skills directory.

Codex discovers a skill as ``<skills-dir>/<skill-name>/SKILL.md``, under a
user-level directory (``$CODEX_HOME/skills``, falling back to ``~/.codex/skills``)
and a project-level directory (``.codex/skills``). This installer puts every
skill from ``skills/`` in front of Codex without forking the repository:

* it links (POSIX) or copies (Windows without symlink privileges) **each skill
  directory individually**, because Codex only looks one level deep -- a single
  link to ``skills/`` would hide every skill behind a directory that has no
  ``SKILL.md`` of its own;
* it records what it did in a hidden receipt, so ``--uninstall`` removes exactly
  the entries it added and never touches a skill it did not install;
* re-running it is a no-op, so it is safe to call again after ``git pull``.

``skills/`` stays the single source of truth: nothing is generated, rewritten or
committed -- ``--project`` installs into the repository *you* run it from, and
this repository never carries a committed ``.codex/`` snapshot.

Exit code is 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = REPO_ROOT / "skills"

RECEIPT_NAME = ".rez-skills.json"
RECEIPT_VERSION = 1
SKILL_FILE = "SKILL.md"
# Codex reads its user-level skills from $CODEX_HOME/skills.
CODEX_SKILLS_SUBDIR = "skills"
# ... and the project-level ones from .codex/skills relative to the cwd.
PROJECT_SKILLS_DIR = Path(".codex") / "skills"

MODES = ("auto", "symlink", "copy")


class InstallError(RuntimeError):
    """A failure that should be reported to the user without a traceback."""


@dataclass
class Receipt:
    """What a previous run of this installer added to a skills directory."""

    source: str
    created_destination: bool
    skills: dict[str, str]

    def to_json(self) -> str:
        payload = {
            "tool": "rez-skills",
            "receipt_version": RECEIPT_VERSION,
            "source": self.source,
            "created_destination": self.created_destination,
            "skills": {name: self.skills[name] for name in sorted(self.skills)},
        }
        return json.dumps(payload, indent=2, sort_keys=True) + "\n"

    @classmethod
    def load(cls, path: Path) -> Receipt | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, NotADirectoryError):
            return None
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        skills = data.get("skills")
        if not isinstance(skills, dict):
            return None
        clean = {
            str(name): str(mode)
            for name, mode in skills.items()
            if isinstance(name, str) and isinstance(mode, str)
        }
        return cls(
            source=str(data.get("source", "")),
            created_destination=bool(data.get("created_destination", False)),
            skills=clean,
        )


@dataclass
class Report:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def codex_home(environ: dict[str, str] | None = None) -> Path:
    """``$CODEX_HOME`` when set, otherwise ``~/.codex``."""
    env = os.environ if environ is None else environ
    configured = env.get("CODEX_HOME", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".codex"


def default_destination(project: bool) -> Path:
    if project:
        return Path.cwd() / PROJECT_SKILLS_DIR
    return codex_home() / CODEX_SKILLS_SUBDIR


def is_under(path: Path, parent: Path) -> bool:
    """True when `path` is `parent` or lives beneath it."""
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def discover_skills(source: Path) -> tuple[list[str], list[str]]:
    """Return (installable skill names, directories skipped for lacking SKILL.md)."""
    if not source.is_dir():
        raise InstallError(f"source directory does not exist: {source}")
    names: list[str] = []
    skipped: list[str] = []
    for entry in sorted(source.iterdir()):
        if not entry.is_dir():
            continue
        if (entry / SKILL_FILE).is_file():
            names.append(entry.name)
        else:
            skipped.append(entry.name)
    if not names:
        raise InstallError(f"no skill directories with {SKILL_FILE} found under {source}")
    return names, skipped


class Installer:
    def __init__(
        self,
        source: Path,
        destination: Path,
        mode: str = "auto",
        *,
        dry_run: bool = False,
        force: bool = False,
        quiet: bool = False,
    ) -> None:
        self.source = source
        self.destination = destination
        self.mode = mode
        self.dry_run = dry_run
        self.force = force
        self.quiet = quiet

        self.receipt_path = destination / RECEIPT_NAME
        self.receipt = Receipt.load(self.receipt_path)
        self.messages: list[str] = []
        # Skills this run is responsible for, name -> install mode. Populated by
        # install() and used to write the receipt.
        self.installed: dict[str, str] = {}

    # -- output -------------------------------------------------------------

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message)

    def note(self, message: str) -> None:
        """A remark that is worth surfacing even in --quiet runs (goes to stderr)."""
        self.messages.append(message)
        print(message, file=sys.stderr)

    # -- filesystem helpers -------------------------------------------------

    def _state(self, link_path: Path, source_skill: Path) -> str:
        """Classify an existing destination entry.

        One of ``absent``, ``ours-link``, ``other-link``, ``dir`` or ``file``.
        """
        if link_path.is_symlink():
            try:
                resolved = link_path.resolve()
            except OSError:
                return "other-link"
            return "ours-link" if resolved == source_skill.resolve() else "other-link"
        if not link_path.exists():
            return "absent"
        if link_path.is_dir():
            return "dir"
        return "file"

    def _owns(self, name: str, link_path: Path, state: str) -> bool:
        """True only when we are confident this entry was installed by us."""
        prior_mode = self.receipt.skills.get(name) if self.receipt else None
        if prior_mode is None:
            # No receipt entry: only an exact link into this clone counts.
            return state == "ours-link"
        if state == "ours-link":
            return True
        if state == "other-link":
            # A link left behind by an earlier install of a clone that moved.
            if prior_mode != "symlink":
                return False
            try:
                resolved = link_path.resolve()
            except OSError:
                return False
            return is_under(resolved, Path(self.receipt.source))
        if state == "dir":
            # A copy: require both the receipt saying so and the shape of a
            # skill, so a same-named directory of the user's own notes is never
            # deleted.
            return prior_mode == "copy" and (link_path / SKILL_FILE).is_file()
        return False

    def _remove_entry(self, link_path: Path) -> None:
        if self.dry_run:
            return
        if link_path.is_symlink() or link_path.is_file():
            link_path.unlink()
        elif link_path.is_dir():
            shutil.rmtree(link_path)

    def _link_one(self, name: str, source_skill: Path, link_path: Path) -> str:
        """Create one destination entry, returning the mode that was used."""
        planned = self.mode if self.mode != "auto" else "symlink"
        if self.dry_run:
            return planned

        if self.mode in ("auto", "symlink"):
            try:
                link_path.symlink_to(source_skill, target_is_directory=True)
            except (OSError, NotImplementedError, ValueError) as exc:
                if self.mode == "symlink":
                    raise InstallError(
                        f"could not symlink {name}: {exc}. Use --mode copy to install copies."
                    ) from exc
                # Windows without developer mode or elevation refuses here.
                self.note(
                    f"note: symlink unavailable for {name} "
                    f"({exc.__class__.__name__}); installing a copy instead"
                )
            else:
                if not (link_path / SKILL_FILE).is_file():
                    raise InstallError(
                        f"{name}: link pointed at {source_skill} but {SKILL_FILE} is not readable"
                    )
                return "symlink"

        shutil.copytree(source_skill, link_path)
        if not (link_path / SKILL_FILE).is_file():
            raise InstallError(f"{name}: copied tree is missing {SKILL_FILE}")
        return "copy"

    # -- install ------------------------------------------------------------

    def install(self) -> Report:
        names, skipped = discover_skills(self.source)
        for name in skipped:
            self.note(f"skipped {self.source.name}/{name}: no {SKILL_FILE}, Codex cannot load it")

        # Decide everything before writing anything, so a refused entry leaves
        # no half-installed destination behind.
        actions: list[tuple[str, str, str]] = []  # (name, action, prior mode)
        refused: list[str] = []
        for name in names:
            state = self._state(self.destination / name, self.source / name)
            if state == "absent":
                actions.append((name, "install", ""))
                continue

            prior_mode = (self.receipt.skills.get(name) if self.receipt else None) or (
                "symlink" if state == "ours-link" else None
            )
            if not self._owns(name, self.destination / name, state):
                if self.force:
                    actions.append((name, "replace", prior_mode or ""))
                else:
                    refused.append(name)
                continue

            # Ours. Refresh it when it no longer matches what a fresh install
            # would produce: a copy (so `git pull` in a clone propagates), a
            # link into a clone that moved, or a link when copies were asked
            # for.
            if state == "ours-link":
                action = "reinstall" if self.mode == "copy" else "keep"
            else:  # dir (a copy) or other-link (a clone that moved)
                action = "reinstall"
            actions.append((name, action, prior_mode or ""))

        if refused:
            raise InstallError(
                "these destination entries already exist and were not installed by this "
                f"script: {', '.join(refused)}\n"
                "  move them aside, or re-run with --force to replace them\n"
                f"  (destination: {self.destination})"
            )

        if self.destination.exists():
            created_destination = bool(self.receipt and self.receipt.created_destination)
        else:
            created_destination = True
            if not self.dry_run:
                self.destination.mkdir(parents=True, exist_ok=True)

        report = Report()
        modes: dict[str, str] = {}
        for name, action, prior_mode in actions:
            source_skill = self.source / name
            link_path = self.destination / name

            if action == "keep":
                modes[name] = prior_mode or "symlink"
                report.unchanged.append(name)
                continue

            if action in ("reinstall", "replace"):
                self.say(f"replacing {link_path}" if action == "replace" else f"refreshing {name}")
                self._remove_entry(link_path)

            modes[name] = self._link_one(name, source_skill, link_path)
            (report.created if action == "install" else report.updated).append(name)

        self.installed = modes
        self._write_receipt(created_destination)
        self._summarize(report)
        return report

    def _write_receipt(self, created_destination: bool) -> None:
        if self.dry_run:
            return
        receipt = Receipt(
            source=str(self.source.resolve()),
            created_destination=created_destination,
            skills=dict(self.installed),
        )
        self.receipt_path.write_text(receipt.to_json(), encoding="utf-8")

    def _summarize(self, report: Report) -> None:
        prefix = "would install" if self.dry_run else "installed"
        for name in report.created:
            mode = self.installed.get(name, self.mode)
            self.say(f"{prefix} {name} ({mode})")
        for name in report.updated:
            mode = self.installed.get(name, self.mode)
            self.say(f"{'would update' if self.dry_run else 'updated'} {name} ({mode})")
        for name in report.unchanged:
            mode = self.installed.get(name, "symlink")
            self.say(f"unchanged {name} ({mode})")

        verb = "would install" if self.dry_run else "installed"
        counts = [f"{len(report.created)} {verb}"]
        if report.updated:
            counts.append(f"{len(report.updated)} updated")
        if report.unchanged:
            counts.append(f"{len(report.unchanged)} already up to date")
        if report.skipped:
            counts.append(f"{len(report.skipped)} skipped")
        self.say(f"{', '.join(counts)} -> {self.destination}")
        if self.dry_run:
            self.say("dry run: nothing was written")

    # -- uninstall ----------------------------------------------------------

    def uninstall(self) -> Report:
        report = Report()
        if not self.destination.exists():
            self.say(f"nothing to uninstall: {self.destination} does not exist")
            return report

        owned: dict[str, str] = {}
        if self.receipt:
            owned.update(self.receipt.skills)
        # Links into this clone count even with no receipt, so a hand-deleted
        # receipt does not strand them.
        for entry in sorted(self.destination.iterdir()):
            if entry.name in owned or not entry.is_symlink():
                continue
            try:
                resolved = entry.resolve()
            except OSError:
                continue
            if is_under(resolved, self.source.resolve()):
                owned[entry.name] = "symlink"

        for name in sorted(owned):
            link_path = self.destination / name
            if not link_path.exists() and not link_path.is_symlink():
                continue
            state = self._state(link_path, self.source / name)
            if not self._owns(name, link_path, state):
                report.skipped.append(name)
                self.note(
                    f"left {link_path} in place: it is not a rez-skills entry "
                    f"(state: {state}); remove it yourself if it is stale"
                )
                continue
            self.say(f"{'would remove' if self.dry_run else 'removed'} {name}")
            self._remove_entry(link_path)
            report.removed.append(name)

        if self.receipt_path.exists():
            self._remove_entry(self.receipt_path)

        if self.dry_run:
            self.say("dry run: nothing was removed")
            return report

        # Drop the skills directory itself only when we created it and it is
        # now empty -- a user's $CODEX_HOME/skills is never removed.
        if self.receipt and self.receipt.created_destination and self.destination.is_dir():
            try:
                if not any(self.destination.iterdir()):
                    self.destination.rmdir()
                    self.say(f"removed empty directory {self.destination}")
            except OSError as exc:  # pragma: no cover - permissions, transient locks
                self.note(f"note: could not remove {self.destination}: {exc}")

        residue = self.residue()
        if residue:
            self.note(f"note: {self.destination} still holds: {', '.join(residue)}")
        return report

    def residue(self) -> list[str]:
        if not self.destination.is_dir():
            return []
        return sorted(entry.name for entry in self.destination.iterdir())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install the rez-skills bundle into a Codex CLI skills directory.",
        epilog=(
            "examples:\n"
            "  python3 scripts/install_codex.py                 # user level, $CODEX_HOME/skills\n"
            "  python3 scripts/install_codex.py --project       # ./.codex/skills\n"
            "  python3 scripts/install_codex.py --mode copy     # copies instead of links\n"
            "  python3 scripts/install_codex.py --uninstall     # clean rollback\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        action="store_true",
        help=f"install into {PROJECT_SKILLS_DIR.as_posix()} of the current directory "
        "instead of the user-level skills directory",
    )
    parser.add_argument(
        "--dest",
        type=Path,
        help="install into this skills directory (overrides --project and $CODEX_HOME)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"skills directory to install from (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="auto",
        help="auto: symlink, falling back to copies when that is refused (default); "
        "symlink: fail instead of copying; copy: always copy",
    )
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="remove the entries this installer added, leaving other skills untouched",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing anything",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace destination entries that this installer did not create",
    )
    parser.add_argument(
        "--print-dest",
        action="store_true",
        help="print the resolved destination skills directory and exit",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="only print warnings and errors",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.print_dest:
        destination = args.dest or default_destination(args.project)
        print(destination)
        return 0

    source = args.source.expanduser()
    destination = (args.dest or default_destination(args.project)).expanduser()

    try:
        installer = Installer(
            source,
            destination,
            args.mode,
            dry_run=args.dry_run,
            force=args.force,
            quiet=args.quiet,
        )
        if args.uninstall:
            installer.uninstall()
        else:
            installer.install()
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
