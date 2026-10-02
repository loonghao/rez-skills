# rez-skills

Agent-friendly skills for [Rez](https://github.com/AcademySoftwareFoundation/rez) — the package and
environment manager used in VFX and animation pipelines.

These skills let AI agents work with Rez accurately instead of guessing: correct version and request
semantics, correct `package.py` structure, and a concrete triage path when a resolve fails.

The repository is packaged as an **agent plugin**, so one install gives you every Rez skill at once.

## Install

Install the plugin from the marketplace that ships in this repository:

```bash
claude plugin marketplace add loonghao/rez-skills
claude plugin install rez@rez-skills
```

Or load it for a single session from a clone:

```bash
claude --plugin-dir ./rez-skills
```

For **Codex CLI**, install the same `skills/` tree into a Codex skills directory:

```bash
git clone https://github.com/loonghao/rez-skills.git
cd rez-skills
python3 scripts/install_codex.py              # user level: $CODEX_HOME/skills
python3 scripts/install_codex.py --project    # ./.codex/skills of the repo you run it in
python3 scripts/install_codex.py --uninstall  # remove exactly what was installed
```

See [Codex CLI](#codex-cli) below for the details, the flags, and how to verify the install.

### ClawHub and OpenClaw

Every skill in this repository is published to [ClawHub](https://clawhub.ai/loonghao) as its own
listing under the `@loonghao` owner, and each one is installed by its own slug:

```bash
openclaw skills install @loonghao/rez-cli   # OpenClaw
clawhub install @loonghao/rez-cli           # ClawHub CLI
```

A published skill lives at `https://clawhub.ai/loonghao/skills/<slug>`, for example
[rez-cli](https://clawhub.ai/loonghao/skills/rez-cli).

ClawHub has **no bundle install**. A skill reference names exactly one skill, and
`openclaw skills install` takes exactly one reference — passing two is rejected with `Too many
arguments for this command.` This repository is a *catalog* repo: one source that publishes twelve
skills, each under its own slug. Installing the whole set is therefore twelve installs, which a
shell loop turns into one command:

```bash
for skill in rez-cli rez-config-plugins rez-core-concepts rez-package-authoring \
             rez-package-commands rez-package-definition rez-package-pitfalls \
             rez-pip-integration rez-python-api rez-resolve rez-resolve-troubleshooting \
             rez-suites-bundles; do
  openclaw skills install "@loonghao/$skill"
done
```

Once installed, `openclaw skills update --all` keeps every ClawHub-installed skill up to date.

The slugs above are the twelve directories under [`skills/`](skills/) and the twelve listings
ClawHub reports for `@loonghao`; `clawhub search rez --prefix` is the check that they still agree.
See [ClawHub publishing](https://docs.openclaw.ai/clawhub/publishing) for the catalog-repo model —
the reusable workflow "calls `skill publish` for each immediate skill folder under root" — and the
[`skills` CLI reference](https://docs.openclaw.ai/cli/skills) for the single-reference install.

## Codex CLI

[Codex CLI](https://github.com/openai/codex) discovers a skill as `<skills-dir>/<skill>/SKILL.md`,
under a user-level directory (`$CODEX_HOME/skills`, or `~/.codex/skills` when `CODEX_HOME` is unset)
and a project-level directory (`.codex/skills`). `scripts/install_codex.py` puts this repository's
`skills/` in front of Codex without forking it:

```bash
git clone https://github.com/loonghao/rez-skills.git
cd rez-skills

python3 scripts/install_codex.py                  # user level (default)
python3 scripts/install_codex.py --project         # ./.codex/skills of the current repository
python3 scripts/install_codex.py --mode copy       # copies instead of links
python3 scripts/install_codex.py --dest PATH       # an explicit skills directory
python3 scripts/install_codex.py --dry-run         # show what would change
python3 scripts/install_codex.py --uninstall       # clean rollback
```

How it behaves:

- **One entry per skill.** Codex only looks one level deep, so each skill directory is linked (or
copied) individually rather than as a single bundle that Codex would not read.
- **Links on POSIX, copies on Windows.** Links mean `git pull` in the clone updates Codex too.
  Windows usually refuses symlinks without developer mode or elevation, so the installer falls back
to copies there; pass `--mode copy` to force copies, or `--mode symlink` to fail instead.
- **Idempotent.** Re-running it after a `git pull` refreshes copies and leaves links alone.
- **Clean rollback.** `--uninstall` removes only the entries this installer added. It never deletes
a skill it did not install: a name it does not recognise is refused at install time unless you pass
`--force`, and left alone at uninstall time.
- **`skills/` stays the source of truth.** Nothing is generated or rewritten. `--project` installs
into the repository you run it from; this repository ships no `.codex/` snapshot.

The installer records what it added in one hidden file, `<skills-dir>/.rez-skills.json`. That is the
receipt `--uninstall` reads; delete it and the installer still finds its own links by following them
back to this clone.

Verify the install:

```bash
python3 scripts/install_codex.py --print-dest          # where the skills went
ls "$(python3 scripts/install_codex.py --print-dest)"  # one directory per skill

codex                                                  # then ask about rez, or run /skills
```

Each skill appears under its own name (`rez-cli`, `rez-resolve`, ...), so a prompt like "why did this
rez resolve fail?" loads `rez-resolve-troubleshooting`.

## What's inside

Skills under [`skills/`](skills/) — all of them load together when the plugin is enabled:

| Skill | Best for |
|-------|----------|
| [`rez-core-concepts`](skills/rez-core-concepts/SKILL.md) | Packages, versions, requests, repositories, search path, variants, ephemerals |
| [`rez-package-definition`](skills/rez-package-definition/SKILL.md) | Authoring `package.py` — attributes, `@early`/`@late`, `requires`, variants |
| [`rez-package-commands`](skills/rez-package-commands/SKILL.md) | The `commands()` section and the rex environment API |
| [`rez-resolve`](skills/rez-resolve/SKILL.md) | Solver internals, `-v` debug output, diagnosing conflicts and cycles |
| [`rez-resolve-troubleshooting`](skills/rez-resolve-troubleshooting/SKILL.md) | Triage when a resolve fails: six causes, the command that confirms each |
| [`rez-package-authoring`](skills/rez-package-authoring/SKILL.md) | Authoring rules — version vs. variant, dependency ranges, build/release loop, anti-patterns |
| [`rez-package-pitfalls`](skills/rez-package-pitfalls/SKILL.md) | The `package.py` execution model — top-level imports, build-time freezing, `F821`, failed builds that still install |
| [`rez-cli`](skills/rez-cli/SKILL.md) | `rez-env`, `rez-build`, `rez-release`, `rez-context` and the rest, with flags |
| [`rez-config-plugins`](skills/rez-config-plugins/SKILL.md) | Configuration layering, key settings, plugin types and discovery |
| [`rez-python-api`](skills/rez-python-api/SKILL.md) | `ResolvedContext`, package/variant objects, programmatic resolves, `.rxt` serialisation |
| [`rez-suites-bundles`](skills/rez-suites-bundles/SKILL.md) | Suites for a shared `PATH` entry, context bundles for a relocatable environment |
| [`rez-pip-integration`](skills/rez-pip-integration/SKILL.md) | Converting pip packages into rez packages with `rez-pip` |

See the [skills README](skills/README.md) for the routing guide and authoring principles.

## Layout

The repository root is the plugin root, which keeps `skills/` as the single source of truth for
both distribution paths:

```
rez-skills/                        # plugin root
├── plugin.json                    # Agent Plugins manifest: what any conforming client reads
├── .claude-plugin/
│   ├── plugin.json                # Claude plugin manifest
│   └── marketplace.json           # marketplace catalog, so `plugin install` works
├── .github/
│   └── schemas/                   # vendored Agent Plugins 1.0.0 JSON Schema
├── scripts/
│   └── install_codex.py           # Codex CLI installer / uninstaller
└── skills/
    └── <skill>/SKILL.md           # one directory per skill
```

Because `skills/` stays where it is, `.github/workflows/sync-skills.yml` keeps discovering and
publishing each skill directory exactly as before.

## Agent Plugins spec

`plugin.json` at the plugin root is the manifest the
[Agent Plugins specification](https://github.com/agentplugins/agent-plugins-spec) defines: a
conforming client reads it and then discovers `skills/<name>/SKILL.md` underneath. That is what
makes one checkout installable by Claude *and* by Codex, or by a client neither of us has met yet,
without any of them needing a fork -- the skills stay where they are and each client reads its own
entry point.

It is checked against the schema the specification publishes, vendored at
`.github/schemas/agent-plugins-1.0.0-plugin.schema.json`, by the `Validate the Agent Plugins
manifest` job of `.github/workflows/sync-skills.yml`, which also has to pass before any skill is
published. The schema sets `additionalProperties: false`, so a key the specification does not
define is rejected rather than carried: `displayName` is a legal Claude manifest key and an illegal
Agent Plugins one, and copying the manifest next door by hand is the easiest way to introduce it.

The two manifests are also checked to name the same plugin, since they are two entry points to one
`skills/` tree. Version parity between them is not asserted yet -- release automation bumps
`.claude-plugin/plugin.json` first, so an assertion added before the two are updated in the same
bump would only turn `main` red between them.

## Validation

```bash
python3 .github/scripts/validate_plugin.py --strict        # plugin + marketplace manifests, skill layout
python3 .github/scripts/validate_codex_install.py --strict # Codex installer: dry run, install, idempotency, rollback
pip install jsonschema
python3 .github/scripts/validate_agent_plugins_spec.py --strict  # plugin.json vs Agent Plugins 1.0.0
pip install "rez==3.4.0"
python3 .github/scripts/validate_skill_commands.py --strict
```

`validate_plugin.py` checks the plugin and marketplace manifests and every skill directory under
`skills/`.

`validate_agent_plugins_spec.py` validates the root `plugin.json` against the vendored Agent
Plugins 1.0.0 schema with a real JSON Schema validator, checks that the vendored file is the
schema it claims to be, and requires the root and Claude manifests to name the same plugin. It
ends by proving it can still fail: it hands the validator a manifest carrying `displayName`, a
name outside the pattern, a name that is empty, a name that is missing, and one with no `$schema`,
and requires every one to be rejected -- so a lax schema copy is reported instead of trusted.

`validate_codex_install.py` drives `scripts/install_codex.py` end to end in throwaway sandboxes: a
dry run must write nothing, an install must put a readable `SKILL.md` at every Codex entry,
re-installing must leave the tree byte-identical, and `--uninstall` must leave the skills directory
empty without touching a skill the installer did not add. It runs on Linux and Windows, because the
link path and the copy path are different code.

`validate_skill_commands.py` checks the commands those skills tell an agent to run. It pulls every
`rez-*` invocation out of the fenced shell blocks **and** the inline `` `code` `` spans of every
`skills/*/SKILL.md`, and checks each command and flag against the parser of the installed rez —
including the options rez registers with `help=argparse.SUPPRESS` (`-v/--verbose`, `--debug`,
`--profile`), which `rez-env --help` never prints — then runs `rez-<cmd> --help` for the seven core
subcommands. In CI this is the `Smoke-test documented rez commands` job of
`.github/workflows/sync-skills.yml`, pinned to rez 3.4.0 through its `REZ_VERSION`.

## Publishing

`.github/workflows/sync-skills.yml` publishes every directory under `skills/` to
[ClawHub](https://clawhub.ai/loonghao). The workflow discovers the skill directories, then runs one
`clawhub skill publish` per directory, so each skill becomes its own listing under `@loonghao`.

- **Pull requests** touching `skills/**`, `plugin.json` or `.claude-plugin/**` validate the plugin
  layout, then run `clawhub skill publish --dry-run` for each skill and validate the receipt. No
  credentials needed, nothing is published.
- **Merges to `main`** touching `skills/**` or `plugin.json`, published releases, and manual
  dispatch run the real publish, which requires the `CLAWHUB_TOKEN` repository secret.

Adding a directory under `skills/` is what adds a listing; nothing publishes the tree as one skill.
Use [ClawHub and OpenClaw](#clawhub-and-openclaw) for the install side of that split.

The ClawHub CLI is pinned to `0.23.3`.

## Source

Content is derived from the Rez 3.4.0 repository — `docs/source/*.rst` and `src/rez/**`.

## License

MIT — see [LICENSE](LICENSE).
