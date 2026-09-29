#!/usr/bin/env python3
"""Semantic smoke-test the rez commands the skills tell an agent to run.

``validate_skill_commands.py`` is a *static* lint: it proves every flag a skill
documents exists on the real parser, and that ``rez-<cmd> --help`` exits 0. That
is not the same as proving the command does what the docs claim.

The gap shipped three times, but only two of them are this lane's to catch --
in those two every token in the command is real:

* ``rez-search '<pkg>-*'`` -- every token is valid, ``--help`` exits 0, and the
  command can never match anything. ``<pkg>-*`` is not a requirement, so
  ``package_search.py`` falls back to globbing *family names* and
  ``fnmatch('foo', 'foo-*')`` is false. It exits 1 with
  ``No matching family found``, which reads like "no such package".
* ``rez-search <pkg> --format`` -- ``--format`` takes a value, so the bare form
  exits 2 with ``expected one argument``. The flag exists; the invocation does
  not work.

The third, ``rez-env --tools`` -- a real rez flag, but one that lives on
``rez-context`` / ``rez-status`` / ``rez-suite`` and not on ``rez-env`` -- is
the static lane's catch, not this one's: the flag is absent from ``rez-env``'s
parser, so it is the static lane that reports there is no such option on
``rez-env``. This lane classifies the line ``no-request`` and skips it, so the
two cover the defect jointly rather than twice. The cases below pin the working
form, ``rez-context --tools``.

This lane closes that gap by doing what a reader does: it builds a real package
fixture, points rez at it, runs the documented commands, and asserts on the
**output**.

Three layers, in order of strength:

1. **Cases** -- a table of commands with expectations about exit status and
   output content, covering the two defect classes that only a real run can
   see, plus the resolve and context paths the skills lean on.
2. **Documented invocations** -- every ``rez-search`` / ``rez-context`` /
   ``rez-env`` / ``rez-config`` / ``rez-status`` / ``rez-depends`` invocation
   the SKILL.md files recommend is extracted with the *same* tokenizer as the
   static lane, normalised onto the fixture, executed, and required to exit 0.
   A documented command that exists but does not work turns this lane red the
   day it appears -- that is what catches ``rez-search <pkg> --format``.
3. **Self-test** -- the lane then turns on itself. Deliberately wrong
   expectations, an injected ``rez-search 'foo-*'`` (the original defect) and a
   bogus documentation anchor must all be reported as failures. A lane that can
   no longer fail is worthless, and this is what proves it still can.

The fixture is hermetic: a temporary directory holds the packages and a
``rezconfig.py`` whose ``packages_path`` is that directory alone. Nothing the
developer has installed locally can leak into a resolve, and a case asserts
that ``rez-config packages_path`` lists exactly the fixture.

Cadence: this lane is heavier than the static one -- it installs rez from PyPI
and performs real resolves, and it refuses to run at all when the installed rez
does not match the ``REZ_VERSION`` the repository pins. Measured end to end, a
full run (22 cases, ~60 documented invocations, 30 self-test controls) takes 75
seconds on a Windows laptop and less on the CI runner, so it runs on every pull
request alongside the static lane rather than being deferred to ``main`` or a
nightly. Keeping it on the PR is what makes a broken documented command show up
next to the diff that introduced it.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"
COMMAND_TIMEOUT = 120

# The extractor, tokenizer and fence walker are shared with the static lane so
# that both agree on what the SKILL.md documents actually say.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import validate_skill_commands as static_lane  # noqa: E402
except ImportError:  # pragma: no cover - a missing sibling is a loud failure
    print(
        "::error::validate_skill_commands.py is missing from .github/scripts/; this "
        "lane shares its tokenizer with it and cannot run without it"
    )
    raise SystemExit(1)

# ---------------------------------------------------------------------------
# The fixture
# ---------------------------------------------------------------------------

# `app` depends on foo so that `rez-depends foo` has something to find, and its
# name deliberately does not start with "f": `rez-search 'f*'` must still match
# exactly one family, or the documented "one family degrades to versions"
# behaviour would not be exercised.
FIXTURE: dict[str, dict[str, list[str]]] = {
    "foo": {"1.0.0": [], "1.1.0": [], "2.0.0": []},
    "bar": {"1.0.0": []},
    "app": {"1.0.0": ["foo-1"]},
    # `alpha` and `beta` want incompatible versions of `gamma`: any request for
    # both fails deterministically, which is what the failed-context cases need.
    "alpha": {"1.0.0": ["gamma-1"]},
    "beta": {"1.0.0": ["gamma-2"]},
    "gamma": {"1.0.0": []},
}

FAMILY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def package_source(name: str, version: str, requires: list[str]) -> str:
    lines = [
        'name = "%s"' % name,
        'version = "%s"' % version,
        'tools = ["%s"]' % name,
    ]
    if requires:
        lines.append("requires = [%s]" % ", ".join('"%s"' % r for r in requires))
    lines += [
        "",
        "def commands():",
        '    env.%s_VERSION = "%s"' % (name.upper(), version),
        "",
    ]
    return "\n".join(lines)


def build_fixture(root: Path) -> Path:
    packages = root / "packages"
    for name, versions in FIXTURE.items():
        if not FAMILY_RE.match(name):
            raise ValueError(f"fixture family {name!r} is not a valid rez package name")
        for version, requires in versions.items():
            target = packages / name / version
            target.mkdir(parents=True, exist_ok=True)
            (target / "package.py").write_text(
                package_source(name, version, requires), encoding="utf-8"
            )
    return packages


# ---------------------------------------------------------------------------
# Cases: the commands the docs recommend, with assertions on their output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Expect:
    """What a command must do. Every field that is set must hold."""

    rc: int | tuple[int, ...] | None = None
    stdout_equals: str | None = None
    stdout_contains: tuple[str, ...] = ()
    stdout_excludes: tuple[str, ...] = ()
    stderr_contains: tuple[str, ...] = ()
    creates: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    argv: tuple[str, ...]
    expect: Expect
    # A regex that must match somewhere in skills/*/SKILL.md. When the docs stop
    # recommending a command, the case is stale and the lane says so instead of
    # silently guarding a shape nobody documents any more.
    anchor: str
    note: str = ""


CONTEXT_FILE = "{ctx}"
FAILED_FILE = "{fail}"
PACKAGES_DIR = "{packages}"

CASES: tuple[Case, ...] = (
    Case(
        "resolve-writes-context",
        ("rez-env", "foo", "-o", CONTEXT_FILE),
        Expect(rc=0, creates=CONTEXT_FILE),
        r"rez-env\s+\S+\s+-o\s+\S+",
        "the non-interactive way to reproduce a resolve",
    ),
    Case(
        "failed-resolve-is-stored",
        ("rez-env", "alpha", "beta", "-o", FAILED_FILE),
        Expect(
            rc=1,
            creates=FAILED_FILE,
            stderr_contains=(
                "The context failed to resolve",
                "gamma-1 <--!--> gamma-2",
            ),
        ),
        r"also stores a FAILED resolve",
        "-o keeps a failed resolve so rez-context can inspect it",
    ),
    # --- rez-search: ranges, globs and the pattern that matches nothing ------
    Case(
        "search-lists-every-version",
        ("rez-search", "foo"),
        Expect(rc=0, stdout_equals="foo-1.0.0\nfoo-1.1.0\nfoo-2.0.0\n"),
        r"rez-search\s+foo",
        "you do not need a version range to list versions",
    ),
    Case(
        "search-range-prefix",
        ("rez-search", "foo-1"),
        Expect(rc=0, stdout_contains=("foo-1.0.0", "foo-1.1.0"), stdout_excludes=("foo-2.0.0",)),
        r"rez-search\s+'foo-1'",
        "a partial version is a prefix range, not a literal",
    ),
    Case(
        "search-range-less-than",
        ("rez-search", "foo<2"),
        Expect(rc=0, stdout_contains=("foo-1.0.0", "foo-1.1.0"), stdout_excludes=("foo-2.0.0",)),
        # The docs quote the range (`rez-search 'foo<2'`), and a shell quoting
        # change must not make this case look stale.
        r"rez-search\s+['\"]?foo<2",
        "an explicit range narrows the same way",
    ),
    Case(
        "search-glob-one-family-lists-versions",
        ("rez-search", "f*"),
        Expect(rc=0, stdout_contains=("foo-1.0.0", "foo-1.1.0", "foo-2.0.0")),
        r"rez-search\s+'f\*'",
        "a glob matching one family still degrades to versions",
    ),
    Case(
        "search-glob-many-families-lists-families",
        ("rez-search", "*"),
        Expect(rc=0, stdout_contains=("bar", "foo"), stdout_excludes=("foo-2.0.0",)),
        r"rez-search\s+'\*'",
        "a glob matching several families degrades to a family list",
    ),
    Case(
        "search-type-package-forces-versions",
        ("rez-search", "foo*", "--type", "package"),
        Expect(rc=0, stdout_contains=("foo-1.0.0", "foo-2.0.0")),
        r"rez-search[^\n]*--type\s+package",
        "--type package overrides the glob-vs-version guess",
    ),
    Case(
        "search-latest",
        ("rez-search", "foo", "--latest"),
        Expect(rc=0, stdout_equals="foo-2.0.0\n"),
        r"rez-search[^\n]*--latest",
        "--latest works with a bare name",
    ),
    Case(
        "search-validates-a-package",
        ("rez-search", "--validate", "foo"),
        Expect(rc=0, stdout_contains=("foo-1.0.0",)),
        r"rez-search\s+--validate",
        "--validate still lists the package it accepted",
    ),
    # --- rez-context: what the resolve actually gave you --------------------
    Case(
        "context-source-order",
        ("rez-context", CONTEXT_FILE, "--so"),
        Expect(
            rc=0,
            stdout_contains=("requested packages:", "resolved packages:", "foo-2.0.0"),
        ),
        r"rez-context\s+context\.rxt\s+--so",
        "the cheapest answer to 'what did I actually get'",
    ),
    Case(
        "context-tools",
        ("rez-context", CONTEXT_FILE, "--tools"),
        Expect(rc=0, stdout_contains=("foo", "foo-2.0.0")),
        r"rez-context\s+--tools",
        "--tools lives on rez-context; the docs once put it on rez-env",
    ),
    Case(
        "context-print-graph",
        ("rez-context", CONTEXT_FILE, "--print-graph"),
        Expect(rc=0, stdout_contains=("digraph", 'label="foo-2.0.0')),
        r"rez-context\s+context\.rxt\s+--print-graph",
        "--print-graph emits dot source on stdout",
    ),
    Case(
        "failed-context-source-order",
        ("rez-context", FAILED_FILE, "--so"),
        Expect(rc=0, stdout_contains=("The context failed to resolve",)),
        r"rez-context[^\n]*--so",
        "a stored failed context is still inspectable",
    ),
    Case(
        "failed-context-print-graph",
        ("rez-context", FAILED_FILE, "--print-graph"),
        Expect(rc=0, stdout_contains=("digraph", "CONFLICT")),
        r"rez-context\s+context\.rxt\s+--print-graph",
        "the dot graph of a failed resolve names the conflict",
    ),
    Case(
        "failed-context-dependency-graph-refuses",
        ("rez-context", FAILED_FILE, "--dependency-graph"),
        Expect(rc=1, stderr_contains=("Cannot perform operation in a failed context",)),
        r"rez-context[^\n]*--dependency-graph",
        "--dependency-graph renders an image and refuses a failed context",
    ),
    # --- rez-env: grouped argument parsing ---------------------------------
    Case(
        "env-trailing-command-needs-a-separator",
        ("rez-env", "foo", "--", "echo", "smoke-ok"),
        Expect(rc=0, stdout_contains=("smoke-ok",)),
        r"rez-env[^\n]*--\s+\S+",
        "rez-env arg_mode is grouped: the command follows --",
    ),
    Case(
        "env-without-a-separator-is-a-request",
        ("rez-env", "foo", "echo", "smoke-ok"),
        Expect(rc=1, stderr_contains=("echo",)),
        r"rez-env[^\n]*--\s+\S+",
        "without -- the command is parsed as another package request",
    ),
    Case(
        "env-two-packages-trailing-command",
        ("rez-env", "foo", "bar", "--", "echo", "smoke-ok"),
        Expect(rc=0, stdout_contains=("smoke-ok",)),
        r"rez-env\s+\S+\s+\S+\s+--\s+\S+",
        "the documented one-shot form with two requests",
    ),
    # --- rez-depends --------------------------------------------------------
    Case(
        "depends-reverse-lookup",
        ("rez-depends", "foo"),
        Expect(rc=0, stdout_contains=("app",)),
        r"rez-depends\s+<pkg>",
        "reverse lookups find the dependent, not just the package itself",
    ),
    # --- hermeticity --------------------------------------------------------
    Case(
        "fixture-is-the-only-repository",
        ("rez-config", "packages_path"),
        Expect(rc=0, stdout_equals=f"- {PACKAGES_DIR}\n"),
        r"rez-config\s+packages_path",
        "no locally installed package can leak into a resolve",
    ),
    Case(
        "status-reports-the-rez-version",
        ("rez-status",),
        Expect(rc=0, stdout_contains=("Using Rez v",)),
        r"rez-status\b",
        "rez-status is the documented first move when the env looks broken",
    ),
)

# Expectation flips used by the self-test: plausible, wrong, and evaluated
# against the output a real case already captured.
CONTROL_EXPECTATIONS: dict[str, Expect] = {
    "search-lists-every-version": Expect(rc=0, stdout_contains=("foo-9.9.9",)),
    "context-tools": Expect(rc=0, stdout_contains=("no-such-tool",)),
    "search-latest": Expect(rc=0, stdout_equals="foo-1.0.0\n"),
    "env-trailing-command-needs-a-separator": Expect(
        rc=0, stderr_contains=("PackageFamilyNotFound",)
    ),
    "fixture-is-the-only-repository": Expect(rc=0, stdout_contains=("site-packages",)),
}

# The command that started all of this: every token is real, and it can never
# match anything. The self-test injects it as a *documented* invocation and
# requires the lane to reject it.
HISTORICAL_BAD_INVOCATION = ("rez-search", "foo-*")


# ---------------------------------------------------------------------------
# Running commands
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Result:
    argv: tuple[str, ...]
    rc: int
    stdout: str
    stderr: str
    timed_out: bool = False
    missing: bool = False


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def subprocess_env(config_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["REZ_CONFIG_FILE"] = str(config_path)
    # A developer's own repository paths must never widen this lane's search
    # path, and an inherited context would make bare `rez-context` ambiguous.
    for name in (
        "REZ_PACKAGES_PATH",
        "REZ_LOCAL_PACKAGES_PATH",
        "REZ_RELEASE_PACKAGES_PATH",
        "REZ_CONTEXT_FILE",
    ):
        env.pop(name, None)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def run_rez(argv: list[str], workdir: Path, env: dict[str, str]) -> Result:
    program = shutil.which(argv[0])
    if program is None:
        return Result(tuple(argv), 127, "", f"{argv[0]} is not on PATH", missing=True)
    try:
        proc = subprocess.run(
            [program, *argv[1:]],
            cwd=str(workdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        # Almost always an accidental interactive shell; 124 keeps it visible.
        return Result(tuple(argv), 124, "", "timed out after %ds" % COMMAND_TIMEOUT, True)
    return Result(
        tuple(argv),
        proc.returncode,
        proc.stdout.replace("\r\n", "\n"),
        proc.stderr.replace("\r\n", "\n"),
    )


def placeholder_values(workdir: Path, packages: Path) -> dict[str, str]:
    return {
        CONTEXT_FILE: str(workdir / "context.rxt"),
        FAILED_FILE: str(workdir / "failed.rxt"),
        PACKAGES_DIR: str(packages),
    }


def render(argv: list[str], workdir: Path, packages: Path) -> list[str]:
    values = placeholder_values(workdir, packages)
    return [values.get(token, token) for token in argv]


def resolve_expect(expect: Expect, values: dict[str, str]) -> Expect:
    """Fill the same placeholders in the assertions as in the argv.

    An expectation is written as `- {packages}\\n` so it reads like the command
    it belongs to; comparing it against real output without substituting first
    would compare the placeholder text with a path.
    """

    def text(value: str) -> str:
        for placeholder, real in values.items():
            value = value.replace(placeholder, real)
        return value

    def maybe(value: str | None) -> str | None:
        return None if value is None else text(value)

    return Expect(
        rc=expect.rc,
        stdout_equals=maybe(expect.stdout_equals),
        stdout_contains=tuple(text(needle) for needle in expect.stdout_contains),
        stdout_excludes=tuple(text(needle) for needle in expect.stdout_excludes),
        stderr_contains=tuple(text(needle) for needle in expect.stderr_contains),
        creates=maybe(expect.creates),
    )


def check(
    expect: Expect, result: Result, workdir: Path | None = None, label: str = ""
) -> list[str]:
    failures: list[str] = []
    prefix = f"{label}: " if label else ""
    joined = " ".join(result.argv)
    if result.missing:
        failures.append(f"{prefix}`{joined}`: {result.stderr}")
        return failures
    if result.timed_out:
        failures.append(f"{prefix}`{joined}` {result.stderr}")
        return failures

    if expect.rc is not None:
        expected = expect.rc if isinstance(expect.rc, tuple) else (expect.rc,)
        if result.rc not in expected:
            failures.append(
                f"{prefix}`{joined}` exited {result.rc}, expected "
                f"{' or '.join(str(code) for code in expected)}"
            )

    if expect.stdout_equals is not None and result.stdout != expect.stdout_equals:
        failures.append(
            f"{prefix}`{joined}` stdout was {result.stdout!r}, expected {expect.stdout_equals!r}"
        )

    for needle in expect.stdout_contains:
        if needle not in result.stdout:
            failures.append(f"{prefix}`{joined}` stdout does not contain {needle!r}")

    for needle in expect.stdout_excludes:
        if needle in result.stdout:
            failures.append(f"{prefix}`{joined}` stdout unexpectedly contains {needle!r}")

    for needle in expect.stderr_contains:
        if needle not in result.stderr:
            failures.append(f"{prefix}`{joined}` stderr does not contain {needle!r}")

    if expect.creates is not None and workdir is not None:
        target = Path(expect.creates)
        if not target.is_absolute():
            target = workdir / target
        if not target.is_file():
            failures.append(f"{prefix}`{joined}` did not create {expect.creates}")

    return failures


# ---------------------------------------------------------------------------
# Layer 2: execute what the docs recommend
# ---------------------------------------------------------------------------

EXECUTABLE_COMMANDS = {
    "rez",
    "rez-search",
    "rez-context",
    "rez-env",
    "rez-config",
    "rez-status",
    "rez-depends",
}

# Commands whose positional arguments are package requests. Every other command
# must run even when its operand names nothing in the fixture: a `rez-config`
# argument is a setting name, and a wrong setting name is drift worth reporting.
PACKAGE_OPERAND_COMMANDS = {"rez-search", "rez-env", "rez-depends"}

# Flags that make an invocation unrunnable here, with the reason why. Keyed by
# subcommand: `-i` is `--input` on rez-env but `--interpret` on rez-context.
NON_RUNNABLE_FLAGS: dict[str, dict[str, str]] = {
    "env": {
        "--patch": "needs an active context (rez says: cannot patch: not in a context)",
        "--patch-rank": "needs an active context",
        "-i": "loads a saved context instead of resolving",
        "--input": "loads a saved context instead of resolving",
        "-s": "would block reading stdin",
        "--stdin": "would block reading stdin",
    },
    "context": {
        "-d": "renders an image with graphviz, which is not installed here",
        "--dependency-graph": "renders an image with graphviz, which is not installed here",
        "-g": "renders an image with graphviz, which is not installed here",
        "--graph": "renders an image with graphviz, which is not installed here",
    },
    "search": {},
    "config": {},
    "depends": {},
    "status": {},
    "rez": {},
}

# Documentation shorthand the static lane already skips, such as `-t/--time`.
SHORTHAND_RE = re.compile(r"^-{1,2}[A-Za-z0-9][A-Za-z0-9-]*/")
PLACEHOLDER_RE = re.compile(r"^<.+>$")

# Doc placeholders and the fixture names they stand for.
SUBSTITUTIONS: dict[str, str] = {
    "<pkg>": "foo",
    "<reqs>": "foo",
    "<setting>": "packages_path",
    "<template>": "{qualified_name}",
    "<fmt>": "{qualified_name}",
    "<path>": PACKAGES_DIR,
    "N": "5",
    "mypackage": "foo",
    "mypkg": "foo",
    "maya": "foo",
    "maya_utils": "foo",
    "my_tool": "foo",
    "my_lib": "foo",
    "geocache": "app",
    "pkg": "foo",
    "bah": "bar",
    "context.rxt": CONTEXT_FILE,
    "c.rxt": CONTEXT_FILE,
    "/tmp/c.rxt": CONTEXT_FILE,
}

NO_VALUE_ACTIONS = (
    argparse._StoreTrueAction,
    argparse._StoreFalseAction,
    argparse._StoreConstAction,
    argparse._AppendConstAction,
    argparse._CountAction,
    argparse._HelpAction,
    argparse._VersionAction,
    argparse._SubParsersAction,
)


@dataclass(frozen=True)
class Cli:
    version: str
    # subcommand (or "rez") -> option string -> whether it consumes a value
    takes_value: dict[str, dict[str, bool]]

    def table_for(self, argv: list[str]) -> dict[str, bool]:
        command = argv[0]
        if command == "rez" and len(argv) > 1 and not argv[1].startswith("-"):
            return self.takes_value.get(argv[1], {})
        return self.takes_value.get(command[len("rez-") :] if command != "rez" else "rez", {})


def load_cli() -> Cli:
    # Importing rez prints advice about pip installs and shell discovery to
    # stderr. It is not this lane's business, and in a CI log it would bury the
    # one line a reader needs.
    with contextlib.redirect_stderr(io.StringIO()):
        import rez
        from rez.cli._main import setup_parser

        parser = setup_parser()
        parser._setup_all_subparsers()

    def flag_map(actions: list[argparse.Action]) -> dict[str, bool]:
        table: dict[str, bool] = {}
        for action in actions:
            consumes = not isinstance(action, NO_VALUE_ACTIONS) and action.nargs != 0
            for option in action.option_strings:
                table[option] = consumes
        return table

    takes_value = {"rez": flag_map(parser._actions)}
    for action in parser._subparsers._actions:
        name_map = getattr(action, "_name_parser_map", None)
        if not name_map:
            continue
        for name, sub_parser in name_map.items():
            takes_value[name] = flag_map(sub_parser._actions)

    return Cli(version=rez.__version__, takes_value=takes_value)


def operands(argv: list[str], table: dict[str, bool]) -> list[str]:
    """Positional arguments: tokens that are neither flags nor flag values."""
    found: list[str] = []
    skip_next = False
    for token in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-") and len(token) > 1:
            option = token.split("=", 1)[0]
            if table.get(option, False) and "=" not in token:
                skip_next = True
            continue
        found.append(token)
    return found


def package_base(token: str) -> str:
    return _PACKAGE_BASE_RE.match(token).group(0) if _PACKAGE_BASE_RE.match(token) else ""


_PACKAGE_BASE_RE = re.compile(r"^[A-Za-z0-9_]+")
GLOB_CHARS = "*?"


def is_resolvable_operand(token: str) -> bool:
    """Can this positional argument stand for something in the fixture?

    Only `rez-search`, `rez-env` and `rez-depends` take package names; a
    `rez-config` operand is a setting name, and a wrong setting name is exactly
    the kind of drift this lane should report rather than skip.
    """
    if token.startswith("{"):
        return True
    token = token.lstrip("!~")
    if any(char in token for char in GLOB_CHARS):
        prefix = token
        for char in GLOB_CHARS:
            prefix = prefix.split(char, 1)[0]
        if not prefix:
            return True
        # `foo-*` is a broken range: the glob follows the version separator, so
        # it must still be recognised as naming `foo` -- and then fail.
        if "-" in prefix:
            return package_base(prefix) in FIXTURE
        return any(name.startswith(prefix) for name in FIXTURE)
    return package_base(token) in FIXTURE


def substitute(argv: list[str]) -> list[str]:
    return [SUBSTITUTIONS.get(token, token) for token in argv]


def normalize_shape(command: str, head: list[str]) -> tuple[str, ...]:
    return tuple([command, *("<pkg>" if token in FIXTURE else token for token in head)])


def shape_of(argv: list[str]) -> tuple[str, ...]:
    trailing = argv.index("--") if "--" in argv else -1
    head = [token for token in (argv[:trailing] if trailing >= 0 else argv)][1:]
    head = [token for token in head if not token.startswith("{")]
    shape = list(normalize_shape(argv[0], head))
    if trailing >= 0:
        shape.append("<cmd>")
    return tuple(shape)


def reroute_output(argv: list[str], workdir: Path, index: int) -> list[str]:
    """Send every `rez-env -o` at a per-invocation file.

    Several documented invocations write `context.rxt`; sharing one path would
    let them clobber the context the cases read.
    """
    if len(argv) < 2 or argv[0] != "rez-env":
        return argv
    out = list(argv)
    for i, token in enumerate(out[:-1]):
        if token in ("-o", "--output") and out[i + 1] != "-":
            out[i + 1] = str(workdir / "invocations" / ("invocation-%02d.rxt" % index))
    return out


@dataclass(frozen=True)
class Invocation:
    argv: tuple[str, ...]
    site: str


def extract_invocations() -> list[Invocation]:
    """Every rez invocation the SKILL.md files recommend, via the static lane's
    own fence walker and tokenizer, so the two lanes cannot disagree about what
    the documents say."""
    found: list[Invocation] = []
    seen: set[tuple[str, ...]] = set()

    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        rel = skill_md.relative_to(REPO_ROOT).as_posix()
        for lineno, origin, line in static_lane.iter_shell_lines(
            skill_md.read_text(encoding="utf-8")
        ):
            spans = (
                static_lane.INLINE_CODE_RE.findall(line)
                if origin == "prose"
                else [line]
            )
            for span in spans:
                tokens = static_lane.tokenize(span)
                if not tokens or not static_lane.COMMAND_RE.match(tokens[0]):
                    continue
                end = len(tokens)
                for i in range(1, len(tokens)):
                    if tokens[i] in static_lane.SHELL_OPERATORS or (
                        static_lane.COMMAND_RE.match(tokens[i])
                    ):
                        end = i
                        break
                argv = tuple(tokens[:end])
                if argv[0] not in EXECUTABLE_COMMANDS or argv in seen:
                    continue
                seen.add(argv)
                found.append(Invocation(argv, f"{rel}:{lineno}"))

    return found


@dataclass
class Executor:
    workdir: Path
    packages: Path
    env: dict[str, str]
    cli: Cli
    case_shapes: set[tuple[str, ...]]
    report: Report
    counts: dict[str, int] = field(default_factory=dict)
    skipped: list[tuple[str, str, str]] = field(default_factory=list)

    def bump(self, key: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + 1

    def skip(self, invocation: Invocation, reason: str, detail: str) -> None:
        self.bump(reason)
        self.skipped.append((" ".join(invocation.argv), reason, f"{invocation.site}: {detail}"))

    def run(self, invocations: list[Invocation]) -> list[tuple[Invocation, Result]]:
        (self.workdir / "invocations").mkdir(parents=True, exist_ok=True)
        executed: list[tuple[Invocation, Result]] = []
        for index, invocation in enumerate(invocations):
            argv = reroute_output(
                render(substitute(list(invocation.argv)), self.workdir, self.packages),
                self.workdir,
                index,
            )
            result = run_rez(argv, self.workdir, self.env)
            executed.append((invocation, result))
        return executed

    def classify(self, invocations: list[Invocation]) -> list[Invocation]:
        """Split the documented invocations into the ones we can execute and the
        ones we can only report on."""
        runnable: list[Invocation] = []
        for invocation in invocations:
            argv = substitute(list(invocation.argv))
            command = argv[0]
            table = self.cli.table_for(argv)
            denied = NON_RUNNABLE_FLAGS.get(
                command[len("rez-") :] if command != "rez" else "rez", {}
            )

            bad_flag = next((flag for flag in argv[1:] if flag in denied), None)
            if bad_flag is not None:
                self.skip(invocation, "not-runnable", f"{bad_flag}: {denied[bad_flag]}")
                continue
            shorthand = next(
                (token for token in argv if SHORTHAND_RE.match(token) or "/" in token[1:]), None
            )
            if shorthand is not None:
                self.skip(invocation, "shorthand", f"{shorthand!r} is documentation shorthand")
                continue
            placeholder = next((token for token in argv if PLACEHOLDER_RE.match(token)), None)
            if placeholder is not None:
                self.skip(invocation, "unmapped", f"{placeholder} has no fixture equivalent")
                continue

            trailing = argv.index("--") if "--" in argv else -1
            head = argv[:trailing] if trailing >= 0 else argv
            if command in PACKAGE_OPERAND_COMMANDS:
                unresolved = next(
                    (token for token in operands(head, table) if not is_resolvable_operand(token)),
                    None,
                )
                if unresolved is not None:
                    self.skip(
                        invocation, "unmapped", f"{unresolved} is not a package in the fixture"
                    )
                    continue

            # Operands of the invocation as written, before the lane injects
            # anything: `rez-search --format` names a flag, while
            # `rez-search foo --format` names a command that does not work.
            named = operands(head, table)
            if command == "rez-env" and not named:
                self.skip(invocation, "no-request", "rez-env without a request is a shell")
                continue
            if command == "rez-depends" and not named:
                self.skip(invocation, "no-request", "rez-depends needs a package to look up")
                continue
            if command == "rez-search" and not named:
                self.skip(invocation, "flag-reference", "the docs name a flag, not a command")
                continue

            # Bare `rez-context ...` reads the active context; here there is
            # none, so it is pointed at the fixture context explicitly.
            if command == "rez-context" and not named:
                argv = [command, CONTEXT_FILE, *argv[1:]]
            # A bare `rez-env <request>` would open an interactive shell and
            # hang, so the lane asks for the context file instead.
            if command == "rez-env" and not any(
                flag in argv for flag in ("--", "-o", "--output", "-c", "--command")
            ):
                argv = [*argv, "-o", CONTEXT_FILE]

            if argv[-1] in table and table[argv[-1]] and not named:
                self.skip(invocation, "abbreviated", f"{argv[-1]} needs a value")
                continue

            if trailing >= 0:
                if shape_of(argv) in self.case_shapes:
                    self.bump("covered-by-case")
                    continue
                self.skip(invocation, "unmapped", "a trailing command the lane cannot assert on")
                continue

            runnable.append(Invocation(tuple(argv), invocation.site))

        return runnable


def case_shapes(cases: tuple[Case, ...]) -> set[tuple[str, ...]]:
    """The invocations the case table already proves, in comparable form.

    Built with `shape_of` on purpose: a documented invocation and a case are the
    same shape only if the very same function says so, so a trailing
    `rez-env mypkg -- printenv MY_VAR` can recognise the case that runs
    `rez-env foo -- echo smoke-ok` without either side growing its own
    normalization rules.
    """
    return {shape_of(list(case.argv)) for case in cases}


# ---------------------------------------------------------------------------
# Documentation anchors
# ---------------------------------------------------------------------------


def load_docs() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(SKILLS_DIR.glob("*/SKILL.md"))
    )


def anchor_failures(cases: tuple[Case, ...], docs: str) -> list[str]:
    failures = []
    for case in cases:
        if re.search(case.anchor, docs) is None:
            failures.append(
                f"case {case.id!r} is not backed by the docs any more: no SKILL.md "
                f"matches {case.anchor!r} -- update the case or restore the document"
            )
    return failures


# ---------------------------------------------------------------------------
# Self-test: prove the lane can still fail
# ---------------------------------------------------------------------------


def self_test(
    results: dict[str, Result],
    workdir: Path,
    env: dict[str, str],
    docs: str,
    executor: Executor,
) -> tuple[list[str], int]:
    """Return (problems, number of controls that fired)."""
    problems: list[str] = []
    fired = 0

    flipped = Expect(rc=127, stdout_contains=("__this text is not in any output__",))
    for case_id, result in results.items():
        # 1. Every case's comparator must reject an impossible expectation.
        if check(flipped, result, workdir):
            fired += 1
        else:
            problems.append(
                f"self-test: case {case_id!r} accepted a deliberately impossible expectation"
            )

    # 2. Wrong-but-plausible expectations on output the cases already captured.
    for case_id, expect in CONTROL_EXPECTATIONS.items():
        result = results.get(case_id)
        if result is None:
            problems.append(f"self-test: control references an unknown case {case_id!r}")
            continue
        if check(expect, result, workdir):
            fired += 1
        else:
            problems.append(
                f"self-test: case {case_id!r} accepted the wrong expectation for "
                f"its own output"
            )

    # 3. The historical defect: injected as if the docs recommended it again.
    injected = Invocation(HISTORICAL_BAD_INVOCATION, "self-test: injected")
    for invocation, result in executor.run([injected]):
        failures = check(Expect(rc=0), result, workdir)
        if failures:
            fired += 1
        else:
            problems.append(
                "self-test: `rez-search 'foo-*'` now exits 0, so the lane can no "
                "longer catch the pattern-matches-nothing defect it exists for"
            )

    # 4. The hermeticity guard must notice a command that cannot succeed.
    missing = run_rez(["rez-search", "no-such-package-xyz"], workdir, env)
    if check(Expect(rc=0), missing, workdir):
        fired += 1
    else:
        problems.append("self-test: a search for a missing package was not reported")

    # 5. A stale documentation anchor must be reported.
    bogus = Case(
        "self-test-stale",
        ("rez-config", "packages_path"),
        Expect(),
        "__never_documented__",
    )
    if anchor_failures((bogus,), docs):
        fired += 1
    else:
        problems.append("self-test: a documentation anchor that matches nothing was accepted")

    return problems, fired


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump",
        action="store_true",
        help="print every documented invocation and how the lane classified it, then exit",
    )
    parser.add_argument(
        "--no-self-test",
        action="store_true",
        help="skip the controls that prove this lane can still fail",
    )
    parser.add_argument(
        "--keep-workdir",
        action="store_true",
        help="leave the fixture and contexts in place for inspection",
    )
    args = parser.parse_args()

    report = Report()
    if not SKILLS_DIR.is_dir():
        print("::error::the skills/ directory does not exist")
        return 1
    documents = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not documents:
        print("::error::no skills/*/SKILL.md files found")
        return 1

    cli = load_cli()
    pinned = os.environ.get("REZ_VERSION")
    if pinned and pinned != cli.version:
        report.errors.append(
            f"REZ_VERSION is {pinned} but the installed rez is {cli.version}; the pins "
            f"must agree or this lane validates something else"
        )
    elif not pinned:
        # Without a pin this lane still runs, but it only proves the documented
        # commands work on whatever rez happens to be installed. Say so, so a
        # locally green run is never mistaken for the pinned one.
        report.warnings.append(
            f"REZ_VERSION is not set, so the pin guard is skipped: this run only "
            f"proves the documented commands work on rez {cli.version}"
        )

    workdir = Path(tempfile.mkdtemp(prefix="rez-semantics-"))
    try:
        packages = build_fixture(workdir)
        config_path = workdir / "rezconfig.py"
        config_path.write_text(
            "packages_path = [%r]\n" % str(packages), encoding="utf-8"
        )
        env = subprocess_env(config_path)

        values = placeholder_values(workdir, packages)
        # --- layer 1: the case table ------------------------------------
        results: dict[str, Result] = {}
        for case in CASES:
            argv = render(list(case.argv), workdir, packages)
            result = run_rez(argv, workdir, env)
            results[case.id] = result
            expect = resolve_expect(case.expect, values)
            report.errors.extend(check(expect, result, workdir, case.id))

        docs = load_docs()
        report.errors.extend(anchor_failures(CASES, docs))

        # --- layer 2: every documented invocation ------------------------
        invocations = extract_invocations()
        executor = Executor(
            workdir=workdir,
            packages=packages,
            env=env,
            cli=cli,
            case_shapes=case_shapes(CASES),
            report=report,
        )
        runnable = executor.classify(invocations)
        executions = executor.run(runnable)
        for invocation, result in executions:
            report.errors.extend(
                check(
                    Expect(rc=0),
                    result,
                    workdir,
                    f"{invocation.site} documents `{' '.join(invocation.argv)}`",
                )
            )

        if not invocations:
            report.errors.append(
                "no rez invocations found in skills/*/SKILL.md -- the extractor is not "
                "reading the documents, so this lane proves nothing"
            )
        if not executions:
            report.errors.append(
                "no documented invocation was executable, so the extraction layer "
                "proves nothing"
            )

        # --- layer 3: the lane must still be able to fail ----------------
        controls = 0
        if not args.no_self_test:
            problems, controls = self_test(results, workdir, env, docs, executor)
            report.errors.extend(problems)

        if args.dump:
            for invocation in invocations:
                argv = " ".join(substitute(list(invocation.argv)))
                print(f"documented\t{invocation.site}\t{argv}")
            for argv, reason, detail in executor.skipped:
                print(f"{reason}\t{argv}\t{detail}")
            for invocation, result in executions:
                print(
                    f"ran\trc={result.rc}\t{invocation.site}\t{' '.join(invocation.argv)}"
                )
            for case in CASES:
                result = results[case.id]
                print(f"case\t{case.id}\trc={result.rc}\t{' '.join(case.argv)}")
            return 0

        for warning in report.warnings:
            print(f"::warning::{warning}")
        for error in report.errors:
            print(f"::error::{error}")

        counts = executor.counts
        detail = ", ".join(f"{key}={counts[key]}" for key in sorted(counts))
        print(
            f"Semantic smoke lane: {len(CASES)} case(s), "
            f"{len(invocations)} documented invocation(s) "
            f"({len(executions)} executed{', ' + detail if detail else ''}), "
            f"rez {cli.version}, fixture at {packages}"
        )
        if not args.no_self_test:
            print(f"Self-test: {controls} control(s) correctly reported as failures")

        if report.errors:
            print(
                f"Semantic validation failed with {len(report.errors)} error(s) "
                f"against rez {cli.version}"
            )
            return 1
        return 0
    finally:
        if args.keep_workdir:
            print(f"workdir kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
