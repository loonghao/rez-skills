#!/usr/bin/env python3
"""Check the README slug lists against the `skills/` directories.

Two READMEs enumerate the published ClawHub slugs by hand: `README.md` in the
"install all sixteen" shell loop, and `skills/README.md` in a second copy of the
same loop. Neither copy is generated. The canonical list is the set of immediate
directories under `skills/`, which is exactly what
`.github/workflows/sync-skills.yml` discovers with
`find skills -mindepth 1 -maxdepth 1` and publishes one listing per directory.

Nothing tied the two halves together before this lane. Adding a directory under
`skills/` produced a new ClawHub listing that neither README mentioned, while the
install loops kept installing the old set -- and every lane stayed green, because
none of them compared the prose against the tree. This is the same class of
defect as a README documenting an install command that does not resolve: the
reader cannot check the claim, and neither could CI.

The comparison is a symmetric difference, not a subset check, so it fires in both
directions:

* a `skills/` directory no README loop names -- a listing that ships but is
  never installed by the documented command;
* a slug a README loop names with no `skills/` directory behind it -- an install
  the reader is told to run that resolves to nothing.

Both README copies are checked. Checking one would leave the other free to drift,
and the second copy is the one a reader in `skills/` is most likely to follow.

The lane also proves it can still fail. It injects a bogus slug into a README copy
that has been parsed in memory and requires the comparison to reject it, and it
asks the extractor to prove it still finds the loops at all. An extractor that
quietly started matching nothing would turn this lane into a no-op that is green
on any tree, which is worse than no lane: it would look like cover.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILLS_DIR = REPO_ROOT / "skills"

README = REPO_ROOT / "README.md"
SKILLS_README = REPO_ROOT / "skills" / "README.md"

# Both copies spell the loop the same way: `for skill in <slug> <slug> ... ; do`
# over one or more continuation lines. Slugs are kebab-case tokens that start
# with `rez-`, which is what separates the loop body from surrounding prose.
LOOP_HEADER = re.compile(r"^for\s+skill\s+in\s+(?P<slugs>[^;]*);", re.MULTILINE | re.DOTALL)
SLUG = re.compile(r"\brez-[a-z0-9]+(?:-[a-z0-9]+)*\b")

BOGUS_SLUG = "rez-not-a-real-skill"


def discover_skill_dirs() -> list[str]:
    """Return the immediate directories under `skills/`, sorted.

    This mirrors the `find skills -mindepth 1 -maxdepth 1` discovery the
    publishing workflow performs, so the lane compares against the same set that
    becomes ClawHub listings.
    """
    if not SKILLS_DIR.is_dir():
        return []
    return sorted(p.name for p in SKILLS_DIR.iterdir() if p.is_dir())


def extract_loop_slugs(text: str) -> list[str]:
    """Return the slugs enumerated by every `for skill in ...` loop in `text`."""
    slugs: list[str] = []
    for match in LOOP_HEADER.finditer(text):
        slugs.extend(SLUG.findall(match.group("slugs")))
    return slugs


def compare(readme_slugs: list[str], skill_dirs: list[str]) -> tuple[list[str], list[str]]:
    """Return (slugs only in the README, slugs only in `skills/`)."""
    readme = set(readme_slugs)
    dirs = set(skill_dirs)
    return sorted(readme - dirs), sorted(dirs - readme)


def validate_readme_text(
    text: str,
    rel: str,
    skill_dirs: list[str],
    errors: list[str],
) -> int:
    """Compare one README copy against `skills/`. Returns the slug count found.

    `rel` labels the copy in messages; `text` is already read, so the self-test
    can drive the same comparison on prose it builds in memory.
    """
    slugs = extract_loop_slugs(text)

    if not slugs:
        errors.append(
            "%s: no `for skill in ...` loop found, so this copy names no slugs and "
            "the drift check has nothing to compare" % rel
        )
        return 0

    duplicates = sorted({s for s in slugs if slugs.count(s) > 1})
    if duplicates:
        errors.append(
            "%s: loop names %s more than once"
            % (rel, ", ".join("`%s`" % s for s in duplicates))
        )

    only_readme, only_dirs = compare(slugs, skill_dirs)
    if only_readme or only_dirs:
        # A symmetric difference, reported in both directions, so the failure
        # names the slug a reader has to act on instead of just counting.
        errors.append(
            "%s: slug list and skills/ directories disagree"
            "%s%s; skills/ is the source of truth, so update the loop or the tree"
            % (
                rel,
                (
                    " -- README only (installs that resolve to nothing): %s"
                    % ", ".join("`%s`" % s for s in only_readme)
                    if only_readme
                    else ""
                ),
                (
                    " -- skills/ only (published but never installed): %s"
                    % ", ".join("`%s`" % s for s in only_dirs)
                    if only_dirs
                    else ""
                ),
            )
        )

    return len(slugs)


def validate_readme(
    path: Path,
    skill_dirs: list[str],
    errors: list[str],
) -> int:
    """Read one README copy from disk and compare it against `skills/`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append("missing README copy to check: %s" % path.relative_to(REPO_ROOT))
        return 0
    except UnicodeDecodeError as exc:
        errors.append("%s is not valid UTF-8: %s" % (path.relative_to(REPO_ROOT), exc))
        return 0

    return validate_readme_text(text, path.relative_to(REPO_ROOT).as_posix(), skill_dirs, errors)


def self_test(errors: list[str]) -> int:
    """Prove the lane still rejects drift. Returns the number of controls that fired."""
    fired = 0

    # 1. A slug with no directory behind it must be reported. Exercises the
    #    extractor and the comparison together, not one function on its own.
    bogus_only: list[str] = []
    validate_readme_text(
        text=(
            "```bash\nfor skill in %s; do\n  openclaw skills install \"@loonghao/$skill\"\ndone\n```"
            % BOGUS_SLUG
        ),
        rel="self-test/README.md",
        skill_dirs=[],
        errors=bogus_only,
    )
    if bogus_only:
        fired += 1
    else:
        errors.append(
            "self-test: a README naming only `%s` was accepted, so the lane can no "
            "longer catch the README-only drift it exists for" % BOGUS_SLUG
        )

    # 2. The other direction: a directory no README names must be reported too.
    dirs_only: list[str] = []
    validate_readme_text(
        text=(
            "```bash\nfor skill in rez-cli; do\n  openclaw skills install \"@loonghao/$skill\"\ndone\n```"
        ),
        rel="self-test/README.md",
        skill_dirs=["rez-cli", BOGUS_SLUG],
        errors=dirs_only,
    )
    if dirs_only:
        fired += 1
    else:
        errors.append(
            "self-test: a skills/ directory no README names was accepted, so the lane "
            "can no longer catch the skills-only drift it exists for"
        )

    # 3. The extractor must find the slugs in a loop it is handed. An extractor
    #    that matches nothing would report an empty list on both sides and call
    #    any tree a pass.
    if extract_loop_slugs("for skill in rez-cli rez-core-concepts; do"):
        fired += 1
    else:
        errors.append(
            "self-test: the extractor found no slugs in a loop that names two, so the "
            "comparison has nothing to compare"
        )

    return fired


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    parser.add_argument(
        "--no-self-test",
        action="store_true",
        help="skip the controls that prove the lane can still fail",
    )
    args = parser.parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    skill_dirs = discover_skill_dirs()
    if not skill_dirs:
        errors.append(
            "no skill directories found under skills/, so the slug lists cannot be "
            "compared against the tree"
        )

    # Both copies, not just the root one: the second copy is the one a reader
    # already inside skills/ is most likely to copy the loop from.
    readme_count = validate_readme(README, skill_dirs, errors)
    skills_readme_count = validate_readme(SKILLS_README, skill_dirs, errors)

    if readme_count and skills_readme_count and readme_count != skills_readme_count:
        errors.append(
            "the two README copies name a different number of slugs (%d in README.md, "
            "%d in skills/README.md), so one of them has drifted from the other"
            % (readme_count, skills_readme_count)
        )

    controls = 0
    if not args.no_self_test:
        controls = self_test(errors)

    for warning in warnings:
        print("::warning::%s" % warning)
    for error in errors:
        print("::error::%s" % error)

    print(
        "Skill slug drift lane: %d slug(s) in README.md, %d in skills/README.md, "
        "against %d directory/directories under skills/"
        % (readme_count, skills_readme_count, len(skill_dirs))
    )
    if not args.no_self_test:
        print("Self-test: %d control(s) correctly reported as failures" % controls)

    if errors:
        print("Skill slug drift validation failed with %d error(s)" % len(errors))
        return 1
    if args.strict and warnings:
        print(
            "Skill slug drift validation failed with %d warning(s) under --strict"
            % len(warnings)
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
