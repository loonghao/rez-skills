#!/usr/bin/env python3
"""Check the `rez-<name>` cross-references the skills write in prose.

``validate_skill_commands.py`` deliberately does *not* check a bare
``rez-<name>`` token in prose. Its docstring says why: in prose a single
unknown ``rez-<name>`` is usually a cross-reference to another skill or to a
Rez docs page, so failing on it would drown that lane in false positives.

That skip is also a blind spot, and something shipped straight through it.
``skills/rez-package-definition/SKILL.md`` told the reader to "See
``rez-package-preprocessing`` in the Rez docs". There is no such rez
subcommand, no such skill in this repository, and no such page in the upstream
rez documentation -- the reference resolved to nothing, which is exactly the
kind of claim a reader cannot check for themselves. Every lane on the pull
request was green while it merged, and the previous round of prose fixes
(``e612a8e``) missed it because that round was a human reading the documents.

This lane closes the gap by giving those tokens somewhere to resolve to. Every
bare ``rez-<name>`` token in prose must be one of:

1. a real rez subcommand, read from the *installed* rez CLI -- the same pin the
   sibling lane installs, so the two cannot disagree;
2. a skill directory under ``skills/``, which is how every skill-to-skill
   cross-reference in this repository is spelled;
3. a page in the upstream rez documentation.

Category 3 is fetched rather than vendored on purpose. A hand-maintained list
of page names would keep agreeing with itself long after the upstream docs had
moved, so it would not catch the drift this lane exists for. The lane reads the
``docs/source`` tree of ``AcademySoftwareFoundation/rez`` at the pinned tag and
fails *closed*: when the upstream tree cannot be read the lane reports an
error instead of quietly resolving nothing and calling it a pass.

A network read also has to be able to fail for real, so the lane ends by
turning on itself the way ``validate_skill_semantics.py`` does. It injects a
reference that resolves to nothing and requires the whole path -- tokenizer,
resolvers and reporters -- to reject it, and it asks each of the three
resolvers on its own to prove it still discriminates. A resolver that has
quietly started accepting everything, or nothing, is reported rather than
trusted.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

# The tokenizer, fence walker and command matcher are shared with the static
# lane, because this lane checks exactly the tokens that one skips: the two
# have to agree on what a "bare rez-<name> in prose" is, or the union of the
# lanes leaves a gap neither of them covers.
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import validate_skill_commands as static_lane  # noqa: E402
except ImportError:  # pragma: no cover - a missing sibling is a loud failure
    print(
        "::error::validate_skill_commands.py is missing from .github/scripts/; this "
        "lane shares its tokenizer with it and cannot run without it"
    )
    raise SystemExit(1)

UPSTREAM_REPO = "AcademySoftwareFoundation/rez"
DOCS_PREFIX = "docs/source/"
DOC_SUFFIXES = (".rst", ".md")
TREE_API = "https://api.github.com/repos/{repo}/git/trees/{ref}?recursive=1"

REQUEST_ATTEMPTS = 3
REQUEST_TIMEOUT = 30


class DocsIndexError(RuntimeError):
    """The upstream documentation tree could not be read."""


# ---------------------------------------------------------------------------
# The three things a bare `rez-<name>` may resolve to
# ---------------------------------------------------------------------------


def normalize(name: str) -> str:
    """The spelling a token and a docs page are compared in.

    `docs/source/package_definition.rst` and the token `rez-package-definition`
    name the same thing, so `_` and `-` have to be the same character here.
    """
    return name.lower().replace("_", "-")


@dataclass(frozen=True)
class Resolvers:
    """Everything a `rez-<name>` token in prose is allowed to mean."""

    subcommands: frozenset[str]
    skills: frozenset[str]
    docs: frozenset[str]

    def resolved_by(self, name: str) -> frozenset[str]:
        """Which categories claim `name`. Empty means it resolves to nothing."""
        found: set[str] = set()
        if name in self.subcommands:
            found.add("subcommand")
        if name in self.skills:
            found.add("skill")
        if normalize(name) in self.docs:
            found.add("docs")
        return frozenset(found)

    def alone(self, category: str) -> "Resolvers":
        """A copy with only `category` populated, for the discriminating tests."""
        return Resolvers(
            subcommands=self.subcommands if category == "subcommand" else frozenset(),
            skills=self.skills if category == "skill" else frozenset(),
            docs=self.docs if category == "docs" else frozenset(),
        )


def skill_names() -> frozenset[str]:
    """Skill directory names, in the same bare spelling as the other categories.

    ``skills/rez-core-concepts`` is referenced as ``rez-core-concepts``, so the
    ``rez-`` the token already carries is what is stored next to the
    subcommand and docs-page names. A skill directory without that prefix
    cannot be named by a ``rez-<name>`` token at all, so it is not something
    this lane could ever resolve a reference to.
    """
    names = set()
    for path in SKILLS_DIR.glob("*/SKILL.md"):
        name = path.parent.name
        if name.startswith("rez-"):
            names.add(name[len("rez-") :])
    return frozenset(names)


def doc_keys(paths: list[str]) -> frozenset[str]:
    """The upstream documentation pages, in comparable spelling.

    A token cannot contain a `/`, so both the path of a page inside
    `docs/source` and its bare name are accepted: `guides/update-to-3` and
    `update-to-3` are both real pages that a reader could be pointed at.
    """
    keys: set[str] = set()
    for path in paths:
        if not path.startswith(DOCS_PREFIX):
            continue
        relative = path[len(DOCS_PREFIX) :]
        if not relative.endswith(DOC_SUFFIXES):
            continue
        stem = relative[: -len(Path(relative).suffix)]
        keys.add(normalize(stem))
        keys.add(normalize(stem.rsplit("/", 1)[-1]))
    return frozenset(keys)


# ---------------------------------------------------------------------------
# The upstream documentation tree
# ---------------------------------------------------------------------------


def github_token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def read_json(url: str, attempts: int = REQUEST_ATTEMPTS) -> dict:
    last = "no attempt was made"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "rez-skills-validate-skill-references",
            },
        )
        token = github_token()
        if token:
            request.add_header("Authorization", "Bearer %s" % token)
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            last = "%s: %s" % (type(exc).__name__, exc)
            if attempt < attempts:
                time.sleep(2**attempt)
    raise DocsIndexError(last)


def fetch_doc_paths(ref: str) -> list[str]:
    """Every path in the upstream rez repository at `ref`.

    Fails closed. A lane whose third category silently degrades into "that is
    not a docs page" would report success on a network hiccup and on a genuine
    broken link alike, which is the blind spot this lane was written to close.
    """
    payload = read_json(TREE_API.format(repo=UPSTREAM_REPO, ref=ref))
    if payload.get("truncated"):
        raise DocsIndexError(
            "the tree was truncated by the API, so the page list would be partial"
        )
    tree = payload.get("tree")
    if not isinstance(tree, list):
        raise DocsIndexError("unexpected API payload: %s" % str(payload)[:200])
    return [entry["path"] for entry in tree if isinstance(entry.get("path"), str)]


def resolve_upstream_tag(version: str) -> str:
    """The git ref that stands for `version` upstream.

    Tried in order: the tag itself, then a `v`-prefixed tag. Returning the ref
    that actually worked keeps the error message honest when neither exists --
    which is also how a typo in the pin gets noticed.
    """
    last = ""
    for candidate in (version, "v" + version):
        try:
            read_json(TREE_API.format(repo=UPSTREAM_REPO, ref=candidate), attempts=1)
        except DocsIndexError as exc:
            last = str(exc)
            continue
        return candidate
    raise DocsIndexError(
        "no upstream ref matches rez %s (tried %s and v%s): %s"
        % (version, version, version, last)
    )


# ---------------------------------------------------------------------------
# Collecting and checking references
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Reference:
    path: Path
    line: int
    token: str

    @property
    def name(self) -> str:
        return self.token[len("rez-") :]

    def format(self) -> str:
        return "%s:%d" % (self.path.as_posix(), self.line)


def collect(skill_md: Path, root: Path) -> list[Reference]:
    """Every bare `rez-<name>` token in the prose of one SKILL.md.

    "Bare" is exactly the shape the static lane skips: an inline span that
    tokenizes to a single rez command token. A span that also names a flag, and
    anything inside a fenced shell block, is that lane's job.
    """
    found: list[Reference] = []
    rel = skill_md.relative_to(root)
    for lineno, origin, line in static_lane.iter_shell_lines(
        skill_md.read_text(encoding="utf-8")
    ):
        if origin != "prose":
            continue
        for span in static_lane.INLINE_CODE_RE.findall(line):
            tokens = static_lane.tokenize(span)
            if tokens and len(tokens) == 1 and static_lane.COMMAND_RE.match(tokens[0]):
                found.append(Reference(rel, lineno, tokens[0]))
    return found


def describe_resolvers(resolvers: Resolvers) -> str:
    return "rez %s, %s skill(s), %s docs page(s)" % (
        len(resolvers.subcommands),
        len(resolvers.skills),
        len(resolvers.docs),
    )


def validate(references: list[Reference], resolvers: Resolvers) -> list[str]:
    errors: list[str] = []
    for reference in references:
        found = resolvers.resolved_by(reference.name)
        if found:
            continue
        hint = static_lane.difflib.get_close_matches(
            reference.name,
            sorted(set(resolvers.subcommands) | set(resolvers.skills) | set(resolvers.docs)),
            n=1,
        )
        suffix = "; did you mean '%s'?" % (hint[0],) if hint else ""
        errors.append(
            "%s: `%s` is not a rez subcommand, a skill in this repository, or an "
            "upstream rez docs page -- the reference points at nothing%s"
            % (reference.format(), reference.token, suffix)
        )
    return errors


# ---------------------------------------------------------------------------
# Self-test: prove the lane can still fail
# ---------------------------------------------------------------------------

# A reference that cannot resolve, used to prove the lane rejects one. It is
# built to be wrong in every category at once: not a subcommand, not a skill,
# and not a word that appears anywhere in the upstream docs tree.
BOGUS_REFERENCE = "rez-not-a-command-not-a-skill-not-a-page"


def discriminating_example(resolvers: Resolvers, category: str) -> str | None:
    """A name that only `category` can resolve, if there is one.

    Used by the self-test to show each resolver still discriminates, without
    hard-coding a name that an upstream rename would break.
    """
    source = {
        "subcommand": resolvers.subcommands,
        "skill": resolvers.skills,
        "docs": resolvers.docs,
    }[category]
    for name in sorted(source):
        if resolvers.alone(category).resolved_by(name) == {category} and len(
            resolvers.resolved_by(name)
        ) == 1:
            return name
    return None


def self_test(references: list[Reference], resolvers: Resolvers) -> tuple[list[str], int]:
    """Return (problems, number of controls that correctly fired)."""
    problems: list[str] = []
    fired = 0

    # 1. The end-to-end path must reject an injected reference. Exercises the
    #    tokenizer, the resolvers and the reporter, not just one function.
    bogus = Reference(Path("skills/self-test/SKILL.md"), 1, BOGUS_REFERENCE)
    if validate(references + [bogus], resolvers):
        fired += 1
    else:
        problems.append(
            "self-test: `%s` was accepted, so the lane can no longer catch the "
            "cross-reference defect it exists for" % BOGUS_REFERENCE
        )

    # 2. Each resolver on its own must still discriminate: accept something it
    #    owns and reject everything else. A resolver that has started accepting
    #    everything would make the lane green on any token at all.
    for category in ("subcommand", "skill", "docs"):
        example = discriminating_example(resolvers, category)
        if example is None:
            problems.append(
                "self-test: no %s-only example exists, so the %s resolver cannot be "
                "shown to discriminate and may be a no-op" % (category, category)
            )
            continue
        single = resolvers.alone(category)
        if single.resolved_by(example) == {category}:
            fired += 1
        else:
            problems.append(
                "self-test: the %s resolver did not accept `%s`, which only it owns"
                % (category, example)
            )
        if single.resolved_by(BOGUS_REFERENCE[len("rez-") :]):
            problems.append(
                "self-test: the %s resolver accepted a reference that resolves to "
                "nothing" % category
            )
        else:
            fired += 1

    # 3. The lane must have found something to check. Zero references means the
    #    extractor stopped reading the documents, not that the docs are clean.
    if not references:
        problems.append(
            "self-test: no bare rez references were found in skills/*/SKILL.md, so "
            "this lane checks nothing"
        )
    else:
        fired += 1

    return problems, fired


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def load_subcommands() -> tuple[frozenset[str], str]:
    # Importing rez prints advice about pip installs and shell discovery to
    # stderr; the sibling lanes swallow it for the same reason.
    with contextlib.redirect_stderr(io.StringIO()):
        cli = static_lane.RezCli.load()
    return frozenset(cli.subcommands), cli.version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump",
        action="store_true",
        help="print every reference and what it resolved to, then exit",
    )
    parser.add_argument(
        "--no-self-test",
        action="store_true",
        help="skip the controls that prove this lane can still fail",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    args = parser.parse_args()

    if not SKILLS_DIR.is_dir():
        print("::error::the skills/ directory does not exist")
        return 1
    documents = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not documents:
        print("::error::no skills/*/SKILL.md files found")
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    try:
        subcommands, version = load_subcommands()
    except ImportError as exc:  # pragma: no cover - environment problem only
        print("::error::rez is not importable, install it before running this check: %s" % exc)
        return 1

    pinned = os.environ.get("REZ_VERSION")
    if pinned and pinned != version:
        # The docs index is fetched at the pin, so a disagreement here would
        # mean the lane validated the prose against a different rez.
        errors.append(
            "REZ_VERSION is %s but the installed rez is %s; the pins must agree or "
            "this lane validates something else" % (pinned, version)
        )
    elif not pinned:
        warnings.append(
            "REZ_VERSION is not set, so the upstream docs index cannot be pinned: this "
            "run compares the prose against rez %s" % version
        )

    resolvers = Resolvers(subcommands, skill_names(), frozenset())
    try:
        ref = resolve_upstream_tag(pinned or version)
        resolvers = Resolvers(
            resolvers.subcommands, resolvers.skills, doc_keys(fetch_doc_paths(ref))
        )
    except DocsIndexError as exc:
        # Fail closed: without the upstream page list the "docs page" category
        # would silently resolve nothing, and a broken cross-reference would be
        # reported as a pass on the strength of a network error.
        errors.append(
            "could not read the upstream rez docs tree for %s from %s (%s); this "
            "lane resolves prose references against it, so it cannot pass without it"
            % (pinned or version, UPSTREAM_REPO, exc)
        )
    if not github_token():
        warnings.append(
            "GH_TOKEN/GITHUB_TOKEN is not set, so the upstream docs tree is read "
            "unauthenticated and may be rate limited"
        )

    references: list[Reference] = []
    for skill_md in documents:
        references.extend(collect(skill_md, REPO_ROOT))

    errors.extend(validate(references, resolvers))

    controls = 0
    if not args.no_self_test and resolvers.docs:
        problems, controls = self_test(references, resolvers)
        errors.extend(problems)
    elif not args.no_self_test:
        errors.append(
            "the upstream docs page list is empty, so the self-test could not run"
        )

    if args.dump:
        for reference in references:
            found = sorted(resolvers.resolved_by(reference.name)) or ["UNRESOLVED"]
            print("%s\t%s\t%s" % (reference.format(), reference.token, "+".join(found)))
        return 0

    for warning in warnings:
        print("::warning::%s" % warning)
    for error in errors:
        print("::error::%s" % error)

    print(
        "Cross-reference lane: %d bare rez reference(s) in %d SKILL.md file(s) against "
        "%s, upstream docs at %s"
        % (
            len(references),
            len(documents),
            describe_resolvers(resolvers),
            pinned or version,
        )
    )
    if not args.no_self_test:
        print("Self-test: %d control(s) correctly reported as failures" % controls)

    if errors:
        print("Cross-reference validation failed with %d error(s)" % len(errors))
        return 1
    if args.strict and warnings:
        print(
            "Cross-reference validation failed with %d warning(s) under --strict"
            % len(warnings)
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
