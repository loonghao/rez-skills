#!/usr/bin/env python3
"""Exercise the Codex installer the way a user would, in throwaway sandboxes.

``scripts/install_codex.py`` runs on someone's machine and writes into their
``$CODEX_HOME``, so the two failure modes that matter are not syntax errors --
they are an install that leaves residue behind and an uninstall that takes a
skill it did not install. Neither shows up in the plugin layout checks.

This script drives the real CLI end to end, in a temporary ``CODEX_HOME`` and a
temporary project directory, and asserts the properties the README promises:

1. ``--dry-run`` writes nothing.
2. An install puts every skill where Codex looks for one:
   ``<dest>/<skill>/SKILL.md``.
3. Re-installing is a no-op, and leaves the tree byte-identical.
4. ``--uninstall`` removes every entry it added and nothing else; the skills
   directory is left empty, and is removed when the installer created it.
5. Both link and copy modes install and roll back cleanly.
6. A skill directory the installer did not create is refused, not overwritten;
   with ``--force`` it is replaced.
7. This repository does not carry a committed ``.codex/`` snapshot -- ``skills/``
   stays the single source of truth.

Every check runs against the real filesystem with the real subprocess, so the
sandbox paths below are the only thing that is faked.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALLER = REPO_ROOT / "scripts" / "install_codex.py"
SKILLS_DIR = REPO_ROOT / "skills"
RECEIPT_NAME = ".rez-skills.json"
SKILL_FILE = "SKILL.md"

# A skill the installer must never touch, used to prove it does not.
FOREIGN_SKILL = "my-own-skill"


class Validator:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks = 0

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def check(self, condition: bool, message: str) -> bool:
        self.checks += 1
        if not condition:
            self.error(message)
        return bool(condition)

    def section(self, title: str) -> None:
        print(f"::group::{title}")

    def endsection(self) -> None:
        print("::endgroup::")


@dataclass
class Sandbox:
    """A throwaway CODEX_HOME plus a throwaway project repository."""

    root: Path
    home: Path
    project: Path
    env: dict[str, str] = field(default_factory=dict)

    @property
    def user_skills(self) -> Path:
        return self.home / "skills"

    @property
    def project_skills(self) -> Path:
        return self.project / ".codex" / "skills"


def expected_skill_names() -> list[str]:
    return sorted(p.name for p in SKILLS_DIR.iterdir() if (p / SKILL_FILE).is_file())


def run_installer(args: list[str], sandbox: Sandbox, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the installer as a subprocess against the sandbox."""
    env = dict(os.environ)
    env.update(sandbox.env)
    # check=False: the return code is the thing under test, not an error to raise on.
    return subprocess.run(
        [sys.executable, str(INSTALLER), *args],
        cwd=str(cwd if cwd is not None else REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def snapshot(directory: Path) -> dict[str, tuple[str, str | None]]:
    """Map every entry under `directory` to (kind, contents-of-SKILL.md).

    'kind' is 'link', 'dir', 'file' or 'missing'; the SKILL.md body is included
    so an idempotency check can prove the content did not change either.
    """
    result: dict[str, tuple[str, str | None]] = {}
    if not directory.is_dir():
        return result
    for entry in sorted(directory.iterdir()):
        if entry.is_symlink():
            kind = "link"
        elif entry.is_dir():
            kind = "dir"
        else:
            kind = "file"
        skill_md = entry / SKILL_FILE
        body: str | None = None
        if skill_md.is_file():
            try:
                body = skill_md.read_text(encoding="utf-8")
            except OSError:
                body = None
        result[entry.name] = (kind, body)
    return result


def list_residue(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(entry.name for entry in directory.iterdir())


def make_sandbox(base: Path, name: str) -> Sandbox:
    root = base / name
    home = root / "codex-home"
    project = root / "project"
    home.mkdir(parents=True)
    project.mkdir(parents=True)
    env = {
        # $CODEX_HOME is what the installer reads; HOME and USERPROFILE are
        # what pathlib consults for `~` on POSIX and Windows respectively.
        "CODEX_HOME": str(home),
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    return Sandbox(root=root, home=home, project=project, env=env)


def check_install_cycle(
    validator: Validator,
    sandbox: Sandbox,
    dest: Path,
    mode: str | None,
    extra_args: list[str],
    label: str,
    project: bool = False,
) -> None:
    """Install -> verify -> install again -> uninstall -> verify it is clean."""
    args = list(extra_args)
    if mode:
        args += ["--mode", mode]
    # --project resolves against the working directory, so every call in a
    # project run has to run inside the sandbox project -- including the dry
    # run, which must never touch this repository.
    cwd = sandbox.project if project else None

    # 1. Dry run writes nothing.
    before = snapshot(dest) if dest.is_dir() else {}
    dry = run_installer([*args, "--dry-run"], sandbox, cwd=cwd)
    validator.check(
        dry.returncode == 0, f"{label}: --dry-run exited {dry.returncode}: {dry.stderr.strip()}"
    )
    after_dry = snapshot(dest) if dest.is_dir() else {}
    validator.check(
        before == after_dry,
        f"{label}: --dry-run changed the destination: {sorted(set(after_dry) - set(before))}",
    )
    validator.check(
        not dest.exists(), f"{label}: --dry-run created {dest}, which it must not"
    )
    if project:
        codex_dir = sandbox.project / ".codex"
        validator.check(
            not codex_dir.exists(),
            f"{label}: --dry-run created {codex_dir}, which it must not",
        )
    for name in expected_skill_names():
        validator.check(
            name in dry.stdout, f"{label}: --dry-run did not report {name}"
        )

    # 2. Install.
    first = run_installer(args, sandbox, cwd=cwd)
    validator.check(first.returncode == 0, f"{label}: install failed: {first.stderr.strip()}")

    expected = expected_skill_names()
    installed = snapshot(dest)
    for name in expected:
        kind, body = installed.get(name, ("missing", None))
        validator.check(kind in ("link", "dir"), f"{label}: {name} was not installed (kind={kind})")
        validator.check(
            body is not None and body.strip() != "",
            f"{label}: {dest.name}/{name}/{SKILL_FILE} is missing or empty",
        )
    if mode == "copy":
        for name in expected:
            kind = installed.get(name, ("missing", None))[0]
            validator.check(kind == "dir", f"{label}: --mode copy left {name} as {kind}, not a copy")

    receipt = dest / RECEIPT_NAME
    validator.check(receipt.is_file(), f"{label}: no receipt at {receipt}")
    if receipt.is_file():
        data = json.loads(receipt.read_text(encoding="utf-8"))
        validator.check(
            sorted(data.get("skills", {})) == expected,
            f"{label}: receipt lists {sorted(data.get('skills', {}))}, expected {expected}",
        )

    # 3. Installing again is a no-op that leaves the tree identical.
    baseline = snapshot(dest)
    second = run_installer(args, sandbox, cwd=cwd)
    validator.check(
        second.returncode == 0,
        f"{label}: re-install exited {second.returncode}: {second.stderr.strip()}",
    )
    repeat = snapshot(dest)
    validator.check(
        baseline == repeat,
        f"{label}: re-install changed the destination: "
        f"{sorted(set(repeat) ^ set(baseline)) or 'skill content differs'}",
    )

    # 4. Uninstall leaves nothing behind.
    uninstall = run_installer([*args, "--uninstall"], sandbox, cwd=cwd)
    validator.check(
        uninstall.returncode == 0,
        f"{label}: uninstall exited {uninstall.returncode}: {uninstall.stderr.strip()}",
    )
    residue = list_residue(dest)
    validator.check(not residue, f"{label}: uninstall left residue in {dest}: {residue}")


def check_foreign_skill_is_safe(validator: Validator, base: Path) -> None:
    """A skill the installer did not add must be refused, then replaced on demand."""
    validator.section("Codex installer: a foreign skill must not be overwritten")
    sandbox = make_sandbox(base, "foreign")
    dest = sandbox.user_skills
    dest.mkdir(parents=True)
    foreign = dest / "rez-cli"
    foreign.mkdir()
    (foreign / "MINE.md").write_text("do not delete\n", encoding="utf-8")

    refused = run_installer([], sandbox)
    validator.check(
        refused.returncode != 0,
        "install must fail when a foreign entry occupies a skill name",
    )
    validator.check(
        (foreign / "MINE.md").is_file(),
        "install destroyed a skill it did not install",
    )

    forced = run_installer(["--force"], sandbox)
    validator.check(forced.returncode == 0, f"--force install failed: {forced.stderr.strip()}")
    validator.check(
        (dest / "rez-cli" / SKILL_FILE).is_file(),
        "--force install did not put SKILL.md at the contested entry",
    )

    uninstall = run_installer(["--uninstall"], sandbox)
    validator.check(uninstall.returncode == 0, f"uninstall failed: {uninstall.stderr.strip()}")
    validator.check(
        not list_residue(dest), f"uninstall left residue in {dest}: {list_residue(dest)}"
    )
    validator.endsection()


def check_unrelated_skill_survives(validator: Validator, base: Path) -> None:
    """A skill the user wrote themselves must outlive install and uninstall."""
    validator.section("Codex installer: an unrelated skill must survive uninstall")
    sandbox = make_sandbox(base, "unrelated")
    dest = sandbox.user_skills
    dest.mkdir(parents=True)
    foreign = dest / FOREIGN_SKILL
    foreign.mkdir()
    body = "# mine\n\nNot installed by rez-skills.\n"
    (foreign / SKILL_FILE).write_text(body, encoding="utf-8")

    install = run_installer([], sandbox)
    validator.check(install.returncode == 0, f"install failed: {install.stderr.strip()}")

    uninstall = run_installer(["--uninstall"], sandbox)
    validator.check(uninstall.returncode == 0, f"uninstall failed: {uninstall.stderr.strip()}")
    validator.check(
        (foreign / SKILL_FILE).is_file(),
        f"uninstall deleted {FOREIGN_SKILL}, which it never installed",
    )
    if (foreign / SKILL_FILE).is_file():
        validator.check(
            (foreign / SKILL_FILE).read_text(encoding="utf-8") == body,
            f"uninstall rewrote {FOREIGN_SKILL}/{SKILL_FILE}",
        )
    validator.check(
        list_residue(dest) == [FOREIGN_SKILL],
        f"uninstall should leave only {FOREIGN_SKILL} in {dest}, found {list_residue(dest)}",
    )
    validator.endsection()


def check_replaced_link_is_left_alone(validator: Validator, base: Path) -> None:
    """A real directory standing where we put a link is the user's, not ours."""
    validator.section("Codex installer: a directory that replaced our link is left alone")
    sandbox = make_sandbox(base, "replaced")
    install = run_installer([], sandbox)
    validator.check(install.returncode == 0, f"install failed: {install.stderr.strip()}")

    replaced = sandbox.user_skills / "rez-cli"
    if validator.check(replaced.is_symlink(), "install did not link rez-cli"):
        # The user swaps our link for their own copy of a skill of the same name.
        replaced.unlink()
        replaced.mkdir()
        body = "# theirs\n\nA hand-maintained rez-cli, not the one we installed.\n"
        (replaced / SKILL_FILE).write_text(body, encoding="utf-8")

    uninstall = run_installer(["--uninstall"], sandbox)
    validator.check(uninstall.returncode == 0, f"uninstall failed: {uninstall.stderr.strip()}")
    validator.check(
        (replaced / SKILL_FILE).is_file(),
        "uninstall deleted a hand-maintained directory that replaced our link",
    )
    if (replaced / SKILL_FILE).is_file():
        validator.check(
            (replaced / SKILL_FILE).read_text(encoding="utf-8") == body,
            "uninstall rewrote a directory that replaced our link",
        )
    residue = list_residue(sandbox.user_skills)
    validator.check(
        residue == ["rez-cli"],
        f"uninstall should leave only the hand-maintained rez-cli, found {residue}",
    )
    validator.endsection()


def check_uninstall_without_receipt(validator: Validator, base: Path) -> None:
    """Deleting the receipt must not strand the links it described."""
    validator.section("Codex installer: uninstall recovers from a missing receipt")
    sandbox = make_sandbox(base, "no-receipt")
    install = run_installer([], sandbox)
    validator.check(install.returncode == 0, f"install failed: {install.stderr.strip()}")
    (sandbox.user_skills / RECEIPT_NAME).unlink(missing_ok=True)

    uninstall = run_installer(["--uninstall"], sandbox)
    validator.check(
        uninstall.returncode == 0,
        f"uninstall without a receipt exited {uninstall.returncode}: {uninstall.stderr.strip()}",
    )
    residue = list_residue(sandbox.user_skills)
    validator.check(
        not residue,
        f"uninstall without a receipt left residue in {sandbox.user_skills}: {residue}",
    )
    validator.endsection()


def check_no_committed_snapshot(validator: Validator) -> None:
    """`skills/` is the source of truth; this repo ships no `.codex/` snapshot."""
    validator.section("Codex installer: the repository ships no .codex/ snapshot")
    snapshot_dir = REPO_ROOT / ".codex"
    validator.check(
        not snapshot_dir.exists(),
        f"{snapshot_dir} exists: project-level installs belong to the consuming repository, "
        "never to this one",
    )
    gitignore = REPO_ROOT / ".gitignore"
    if validator.check(gitignore.is_file(), ".gitignore is missing"):
        lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
        validator.check(
            "/.codex/" in lines,
            ".gitignore does not ignore /.codex/, so a local install could be committed by accident",
        )
    validator.endsection()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    args = parser.parse_args()

    validator = Validator()

    if not validator.check(INSTALLER.is_file(), f"missing installer: {INSTALLER}"):
        print(f"::error::missing installer: {INSTALLER}")
        return 1
    if not validator.check(
        bool(expected_skill_names()), f"no skills with {SKILL_FILE} found under {SKILLS_DIR}"
    ):
        print(f"::error::no skills with {SKILL_FILE} found under {SKILLS_DIR}")
        return 1

    base = Path(tempfile.mkdtemp(prefix="rez-skills-codex-"))
    try:
        validator.section("Codex installer: user-level install (link mode)")
        sandbox = make_sandbox(base, "user-link")
        check_install_cycle(validator, sandbox, sandbox.user_skills, None, [], "user/link")
        validator.endsection()

        validator.section("Codex installer: user-level install (copy mode)")
        sandbox = make_sandbox(base, "user-copy")
        check_install_cycle(validator, sandbox, sandbox.user_skills, "copy", [], "user/copy")
        validator.endsection()

        validator.section("Codex installer: project-level install")
        sandbox = make_sandbox(base, "project")
        check_install_cycle(
            validator,
            sandbox,
            sandbox.project_skills,
            None,
            ["--project"],
            "project/link",
            project=True,
        )
        validator.endsection()

        check_foreign_skill_is_safe(validator, base)
        check_unrelated_skill_survives(validator, base)
        check_replaced_link_is_left_alone(validator, base)
        check_uninstall_without_receipt(validator, base)
    finally:
        shutil.rmtree(base, ignore_errors=True)

    check_no_committed_snapshot(validator)

    for warning in validator.warnings:
        print(f"::warning::{warning}")
    for error in validator.errors:
        print(f"::error::{error}")

    status = "failed" if validator.errors else "passed"
    suffix = " with warnings" if validator.warnings and not validator.errors else ""
    print(f"Codex installer validation {status}{suffix}: {validator.checks} check(s)")

    if validator.errors:
        return 1
    if args.strict and validator.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
