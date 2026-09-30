#!/usr/bin/env python3
"""Fail when the rez version this repository pins has fallen behind upstream.

``REZ_VERSION`` is a hand-typed constant: it is what the CI lanes install, what
``validate_skill_commands.py`` checks every documented command against, and what
``validate_skill_references.py`` reads the upstream documentation at. Nothing
watches it. Right now it happens to be the newest upstream release, and the day
a newer one ships this repository will keep documenting the older CLI without
any signal that the skills have started to drift.

This is the signal. It runs on a schedule rather than on every pull request on
purpose: a new rez release must not turn an unrelated pull request red. On a
drift the job fails with the exact line to change, so the bump is a deliberate
decision with an owner rather than a surprise in someone else's diff.

Only stable releases count. A prerelease is reported as a warning, because
documenting against a release candidate is a choice a maintainer may reasonably
want to defer, but the pin should still be revisited once it is final.

The check fails closed: an upstream list it cannot read, or one with no stable
release in it, is an error rather than a pass. A "no news" result it cannot
substantiate is exactly the false green this job exists to prevent.

Exit code is 0 when the pin is current, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "AcademySoftwareFoundation/rez"
RELEASES_API = "https://api.github.com/repos/{repo}/releases?per_page=100"

# The pin lives in the workflow that installs rez, so it is read from there
# rather than declared a second time here: two copies of a hand-typed version
# is how a drift check ends up comparing the wrong thing.
DEFAULT_WORKFLOW = ".github/workflows/sync-skills.yml"
PIN_RE = re.compile(r"^\s*REZ_VERSION:\s*[\"']?([^\"'\s#]+)[\"']?\s*$", re.MULTILINE)

REQUEST_ATTEMPTS = 3
REQUEST_TIMEOUT = 30


class UpstreamError(RuntimeError):
    """The upstream release list could not be read."""


def read_pinned_version(path: Path) -> str:
    match = PIN_RE.search(path.read_text(encoding="utf-8"))
    if match is None:
        raise UpstreamError(
            "no REZ_VERSION pin found in %s, so there is nothing to compare" % path
        )
    return match.group(1)


def github_token() -> str:
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def read_json(url: str, attempts: int = REQUEST_ATTEMPTS) -> object:
    last = "no attempt was made"
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "rez-skills-check-rez-version-drift",
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
    raise UpstreamError(last)


def parse_version(text: str) -> tuple[int, ...]:
    """The numeric parts of a tag, so `3.10.0` sorts above `3.9.0`."""
    head = str(text).lstrip("v").split("-", 1)[0]
    parts = []
    for chunk in head.split("."):
        digits = "".join(char for char in chunk if char.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) if parts else (0,)


def fetch_releases() -> list[tuple[str, bool, str]]:
    """Every published release as (tag_name, prerelease, created_at)."""
    payload = read_json(RELEASES_API.format(repo=UPSTREAM_REPO))
    if not isinstance(payload, list):
        raise UpstreamError("unexpected API payload: %s" % str(payload)[:200])
    releases = []
    for entry in payload:
        if not isinstance(entry, dict) or not isinstance(entry.get("tag_name"), str):
            continue
        releases.append(
            (
                entry["tag_name"],
                bool(entry.get("prerelease")),
                str(entry.get("created_at") or ""),
            )
        )
    releases.sort(key=lambda item: (parse_version(item[0]), item[2]), reverse=True)
    return releases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workflow",
        default=DEFAULT_WORKFLOW,
        help="the workflow holding the REZ_VERSION pin (default: %(default)s)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    args = parser.parse_args()

    workflow = Path(args.workflow)
    if not workflow.is_file():
        print("::error::%s does not exist, so the pin cannot be read" % workflow)
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    try:
        pinned = read_pinned_version(workflow)
        releases = fetch_releases()
    except UpstreamError as exc:
        print(
            "::error::could not read the upstream rez releases from %s (%s); this "
            "job cannot claim the pin is current without them" % (UPSTREAM_REPO, exc)
        )
        return 1

    stable = [tag for tag, prerelease, _ in releases if not prerelease]
    prereleases = [tag for tag, prerelease, _ in releases if prerelease]
    if not stable:
        print(
            "::error::no stable release was found in the upstream rez release list, so "
            "the pin cannot be compared"
        )
        return 1

    latest = stable[0]
    ahead = [tag for tag in prereleases if parse_version(tag) > parse_version(pinned)]
    if ahead:
        warnings.append(
            "upstream has newer prerelease(s) than the pin: %s" % ", ".join(ahead)
        )
    if not github_token():
        warnings.append(
            "GH_TOKEN/GITHUB_TOKEN is not set, so the release list is read "
            "unauthenticated and may be rate limited"
        )

    if latest != pinned and parse_version(latest) > parse_version(pinned):
        errors.append(
            "REZ_VERSION is %s but upstream %s has released %s; set REZ_VERSION to "
            "%s in %s, then re-run the skill lanes -- the skills may be documenting "
            "commands that changed in between" % (pinned, UPSTREAM_REPO, latest, latest, workflow)
        )
    elif latest != pinned:
        # The pin is ahead of the newest release: a local build, or a retraction.
        warnings.append(
            "REZ_VERSION %s is ahead of the newest upstream release %s" % (pinned, latest)
        )

    for warning in warnings:
        print("::warning::%s" % warning)
    for error in errors:
        print("::error::%s" % error)

    print(
        "rez version drift: pinned %s, newest stable upstream release %s, %d stable "
        "and %d prerelease release(s) read from %s"
        % (pinned, latest, len(stable), len(prereleases), UPSTREAM_REPO)
    )

    if errors:
        print("Version drift check failed with %d error(s)" % len(errors))
        return 1
    if args.strict and warnings:
        print("Version drift check failed with %d warning(s) under --strict" % len(warnings))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
