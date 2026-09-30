#!/usr/bin/env python3
"""Check the root plugin manifest against the Agent Plugins 1.0.0 schema.

This repository is already an agent plugin: `.claude-plugin/plugin.json` is
what `claude plugin install` reads, `scripts/install_codex.py` is what puts the
same `skills/` tree in front of Codex, and `validate_plugin.py` checks both.
What is missing is the part that is not Claude-specific: a plugin root with a
`plugin.json` at its top level, which is the layout the
[Agent Plugins specification](https://github.com/agentplugins/agent-plugins-spec)
says any conforming client should be able to load -- a client reads
`plugin.json`, then discovers `skills/<name>/SKILL.md` underneath it. That is
the shape that lets a Codex or Claude plugin install work without either of
them needing a fork of this repository.

A manifest that merely looks like the one in the spec's quick start is not the
same thing as a conforming one, so this lane does not hand-roll a check of the
fields we happen to care about. It validates `plugin.json` against the schema
the specification publishes, vendored under `.github/schemas/`, using a real
JSON Schema 2020-12 validator. That matters because the interesting requirement
is a negative one: the schema sets `additionalProperties: false`, so a manifest
carrying any key outside the specification is *not* conforming -- including
`displayName`, which the Claude manifest next door legitimately uses and which
would be the single easiest thing to copy across by hand.

The vendored schema is also checked for its own identity. Its `$id` has to
agree with the `$schema` const it enforces, the manifest's `$schema` has to
name that same URL, and the draft the schema declares has to be the one this
lane validates with. Without that guard, replacing the vendored file with a lax
or unrelated schema would turn this lane into something that passes on any
input at all.

Two checks are deliberately *not* here:

- Version parity with `.claude-plugin/plugin.json`. Release automation bumps
  that manifest first, so a parity assertion added now would make `main` red
  for as long as the two files are updated by different mechanisms. It lands
  together with the release automation change that keeps the root manifest in
  the same version bump, not before it.
- Anything about `skills/*/SKILL.md` layout, the Claude manifest's own shape,
  or marketplace source coverage. `validate_plugin.py` already owns all three;
  duplicating them here would give the same defect two places to hide.

Every lane here has to be able to fail for real, so this one ends by turning on
itself the way `validate_skill_references.py` does. It requires a minimal
valid manifest to pass and four malformed ones to be rejected, so a schema copy
that has quietly started accepting everything is reported rather than trusted.

Exit code is 0 when everything validates, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - environment problem only
    jsonschema = None

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MANIFEST = REPO_ROOT / "plugin.json"
VENDORED_SCHEMA = (
    REPO_ROOT / ".github" / "schemas" / "agent-plugins-1.0.0-plugin.schema.json"
)
CLAUDE_MANIFEST = REPO_ROOT / ".claude-plugin" / "plugin.json"

SPEC_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"

# The JSON Schema draft this lane validates with, and the one the vendored
# schema has to declare. They are held together on purpose: `validate_manifest`
# builds a `Draft202012Validator` unconditionally, so a schema that declared a
# different draft would be checked under semantics it was not written for, and
# the keywords it used to express its rules could mean something else.
DRAFT_URL = "https://json-schema.org/draft/2020-12/schema"


def load_json(path: Path, label: str) -> tuple[object | None, str | None]:
    """Read `path`, returning (value, error) so callers can report both."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "%s (%s) does not exist" % (label, path.as_posix())
    except OSError as exc:
        return None, "%s (%s) could not be read: %s" % (label, path.as_posix(), exc)
    try:
        return json.loads(text), None
    except ValueError as exc:
        return None, "%s (%s) is not valid JSON: %s" % (label, path.as_posix(), exc)


def schema_identity_errors(schema: object) -> list[str]:
    """Reject a vendored copy that is not the 1.0.0 plugin schema.

    The manifest is validated against whatever sits in `.github/schemas/`, so
    the file being the right one is a premise of every check below rather than
    something any of them can establish. The `$id`/`const` pair is the
    cheapest thing that is hard to get wrong by accident: editing either one
    alone leaves them disagreeing, and this is what notices.
    """
    if not isinstance(schema, dict):
        return [
            "the vendored Agent Plugins schema must be a JSON object, found %s"
            % type(schema).__name__
        ]
    schema_id = schema.get("$id")
    if not isinstance(schema_id, str) or not schema_id:
        return [
            "the vendored Agent Plugins schema declares no $id, so the manifest's "
            "$schema cannot be checked against it"
        ]
    const = schema.get("properties", {}).get("$schema", {}).get("const")
    if const != schema_id:
        return [
            "the vendored Agent Plugins schema declares $id %r but requires $schema to "
            "be %r; one of the two is not the 1.0.0 plugin schema" % (schema_id, const)
        ]
    draft = schema.get("$schema")
    if draft != DRAFT_URL:
        return [
            "the vendored Agent Plugins schema declares $schema %r but this lane "
            "validates with a %s validator" % (draft, DRAFT_URL)
        ]
    return []


def validate_manifest(manifest: object, schema: object) -> list[str]:
    """Every way `manifest` breaks the vendored schema, as text.

    A pure function rather than a step in the run, because the self-test at the
    bottom of this file feeds it manifests it makes up. The lane and its own
    controls therefore cannot drift apart.
    """
    if not isinstance(manifest, dict):
        return [
            "the root plugin manifest must be a JSON object, found %s"
            % type(manifest).__name__
        ]
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(
        validator.iter_errors(manifest),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    return [
        "%s: %s"
        % ("/".join(str(part) for part in error.absolute_path) or "<root>", error.message)
        for error in errors
    ]


def name_parity_errors(manifest: dict, claude_manifest: dict) -> list[str]:
    """The two manifests must name the same plugin.

    They are two entry points to one `skills/` tree. If they disagree, the
    plugin installs under whichever name the client happened to read and the
    other name silently stops working, which is an install bug a reader of
    either manifest cannot see.
    """
    ours = manifest.get("name")
    theirs = claude_manifest.get("name")
    if not isinstance(theirs, str) or not theirs:
        return [
            ".claude-plugin/plugin.json has no name, so the plugin root cannot be "
            "checked against it"
        ]
    if not isinstance(ours, str) or not ours:
        return []  # already reported as a schema violation
    if ours != theirs:
        return [
            "plugin.json names the plugin %r but .claude-plugin/plugin.json names it "
            "%r; the same skills/ tree must not ship under two names" % (ours, theirs)
        ]
    return []


# ---------------------------------------------------------------------------
# Self-test: prove the lane can still fail
# ---------------------------------------------------------------------------

# The minimal conforming manifest from the specification's quick start: with
# `additionalProperties: false`, these two keys are also the only ones a
# conforming manifest is *required* to have.
MINIMAL_MANIFEST: dict[str, object] = {"$schema": SPEC_SCHEMA_URL, "name": "rez"}

# Manifests that must be rejected. The first is the one this lane exists for:
# `displayName` is a legal Claude manifest key and an illegal Agent Plugins
# one, and copying the neighbouring manifest across by hand is the likeliest
# way to introduce it.
#
# The last three pin the schema's *structural* minimum, not just its
# vocabulary. `required`, `minLength` and `pattern` are the rules a manifest in
# this repository cannot break by construction -- every key it carries is one
# the specification allows -- so a vendored copy that quietly stopped enforcing
# them would keep every real manifest green and there would be nothing here to
# notice. Feeding the validator a nameless, an empty-named and a schema-less
# manifest is what turns that silent gap into a failure.
REJECTED_ARMS: tuple[tuple[str, dict], ...] = (
    (
        "an extra top-level key the specification does not define",
        dict(MINIMAL_MANIFEST, displayName="Rez Skills"),
    ),
    (
        "a $schema that is not the 1.0.0 plugin schema",
        dict(MINIMAL_MANIFEST, **{"$schema": "https://json.schemastore.org/claude-code-plugin.json"}),
    ),
    (
        "a name outside the lowercase kebab-case pattern",
        dict(MINIMAL_MANIFEST, name="Rez_Skills"),
    ),
    (
        "a nested object where a string is required",
        dict(MINIMAL_MANIFEST, version={"major": 1}),
    ),
    (
        "no name at all",
        {key: value for key, value in MINIMAL_MANIFEST.items() if key != "name"},
    ),
    (
        "an empty name",
        dict(MINIMAL_MANIFEST, name=""),
    ),
    (
        "no $schema at all",
        {key: value for key, value in MINIMAL_MANIFEST.items() if key != "$schema"},
    ),
)


def self_test(schema: object) -> tuple[list[str], int]:
    """Return (problems, number of controls that correctly fired)."""
    problems: list[str] = []
    fired = 0

    accepted = validate_manifest(MINIMAL_MANIFEST, schema)
    if accepted:
        problems.append(
            "self-test: the minimal conforming manifest was rejected (%s), so this lane "
            "is not validating against the Agent Plugins schema"
            % "; ".join(accepted)
        )
    else:
        fired += 1

    for label, arm in REJECTED_ARMS:
        if validate_manifest(arm, schema):
            fired += 1
        else:
            problems.append(
                "self-test: a manifest with %s was accepted, so this lane can no longer "
                "catch the defect it exists for" % label
            )

    return problems, fired


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate plugin.json against the Agent Plugins 1.0.0 schema."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="treat warnings as errors",
    )
    parser.add_argument(
        "--no-self-test",
        action="store_true",
        help="skip the controls that prove this lane can still fail",
    )
    args = parser.parse_args()

    if jsonschema is None:
        print(
            "::error::jsonschema is not installed; install it (pip install jsonschema) "
            "before running this check"
        )
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    schema, schema_error = load_json(VENDORED_SCHEMA, "the vendored Agent Plugins schema")
    manifest, manifest_error = load_json(MANIFEST, "the root plugin manifest")

    if schema_error:
        errors.append(schema_error)
    if manifest_error:
        errors.append(manifest_error)
    if errors:
        report(errors, warnings, controls=0)
        return 1

    errors.extend(schema_identity_errors(schema))
    errors.extend(validate_manifest(manifest, schema))

    schema_id = schema.get("$id") if isinstance(schema, dict) else None
    if isinstance(schema_id, str) and schema_id and isinstance(manifest, dict):
        declared = manifest.get("$schema")
        if declared != schema_id:
            # Caught by the schema too when the vendored copy is intact; this
            # is the case that matters when it is not.
            errors.append(
                "plugin.json declares $schema %r but is validated against the vendored "
                "schema whose $id is %r; the two must name the same specification"
                % (declared, schema_id)
            )

    claude_manifest, claude_error = load_json(
        CLAUDE_MANIFEST, "the Claude plugin manifest"
    )
    if claude_error:
        errors.append(claude_error)
    elif isinstance(manifest, dict) and isinstance(claude_manifest, dict):
        errors.extend(name_parity_errors(manifest, claude_manifest))

    if isinstance(manifest, dict) and not manifest.get("version"):
        warnings.append(
            "plugin.json carries no version, so a client that installs it cannot tell "
            "one release from another"
        )

    controls = 0
    if not args.no_self_test:
        problems, controls = self_test(schema)
        errors.extend(problems)

    report(errors, warnings, controls=0 if args.no_self_test else controls)

    if errors:
        print("Agent Plugins spec validation failed with %d error(s)" % len(errors))
        return 1
    if args.strict and warnings:
        print(
            "Agent Plugins spec validation failed with %d warning(s) under --strict"
            % len(warnings)
        )
        return 1
    return 0


def report(errors: list[str], warnings: list[str], controls: int) -> None:
    for warning in warnings:
        print("::warning::%s" % warning)
    for error in errors:
        print("::error::%s" % error)
    print(
        "Agent Plugins spec lane: %s, validated against %s"
        % (
            MANIFEST.relative_to(REPO_ROOT).as_posix(),
            VENDORED_SCHEMA.relative_to(REPO_ROOT).as_posix(),
        )
    )
    print("Self-test: %d control(s) correctly reported as failures" % controls)


if __name__ == "__main__":
    sys.exit(main())
