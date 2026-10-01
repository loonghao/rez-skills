# rez — AI Agent Skills

Agent-friendly skills for **[Rez](https://github.com/AcademySoftwareFoundation/rez)** (3.4.0), the
package and environment manager used in VFX/animation pipelines.

Source material: the Rez repository — `docs/source/*.rst` and `src/rez/**` (notably
`solver.py`, `rezconfig.py`, `rex.py`, `build_system.py`, `cli/_util.py`).

`skills/` is the **canonical source**, and the repository root is an **agent plugin**:
`.claude-plugin/plugin.json` names it and `.claude-plugin/marketplace.json` lists it, so installing
the plugin installs every skill below in one step. Project-level agent directories (`.agents/`, `.claude/`,
`.cursor/`, and friends) are compatibility snapshots generated from it, not independently
maintained sources.

## Skill Authoring Principles

These skills teach agents to be precise, scoped, and token-aware:

- Read the narrowest relevant file, symbol, or command output first.
- Prefer non-interactive, machine-scoped commands (`-o`, `--format`, `--so`, `--print-graph`) over
  opening a subshell or dumping a whole build log.
- Scope command output before reading it: `rez-config <setting>`, `rez-context --so`,
  `rez-search --format`, `rez-depends <pkg>`.
- Avoid broad repo dumps, full logs, and grepping every `package.py` when `rez-depends` exists.
- Validate with the cheapest useful focused check, and escalate (`-v`, `--fail-graph`) only when
  that is insufficient.
- Treat `skills/` as canonical and agent directories as generated snapshots.

## Available Skills

| Skill | Description | Best for |
|-------|-------------|----------|
| **rez-core-concepts** | Packages, versions, requests, repositories, search path, implicits, variants, ephemerals | "What is Rez?", version/request semantics, where packages come from |
| **rez-package-definition** | `package.py` authoring — attributes, `@early`/`@late`, `requires`, `build_requires`, variants | Writing or reviewing a package definition |
| **rez-package-commands** | The `commands()` section and rex API — `env`, expansion, execution order, build branching | Setting env vars, exposing tools, build-time behavior |
| **rez-resolve** | Solver internals, `-v` debug output, conflicts/cycles/reductions, graphs, caching | "How does the solver work?", reading `-v` traces |
| **rez-resolve-troubleshooting** | Diagnosing resolve failures — six causes, the command that confirms each, cache and filter pitfalls | "The context failed to resolve", a conflict to attribute, a package rez will not use |
| **rez-package-authoring** | Authoring rules and decisions — version vs. variant, range width, `requires`/`variants`/`commands`, build/release loop, anti-patterns | Writing or reviewing a `package.py`, deciding whether a change needs a new version or variant |
| **rez-package-pitfalls** | The `package.py` execution model — top-level imports and serialization, build-time freezing, `F821` on rez-injected names, failed builds that still install | "invalid syntax" from a package you did not write that way, a value frozen on the build machine, reviewing module scope before release |
| **rez-cli** | Command reference — `rez-env`, `rez-build`, `rez-release`, `rez-context`, `rez-search`, flags | "What is the command for…", build/release/test loops |
| **rez-config-plugins** | Config layering, merge rules, key settings, the seven plugin types and discovery | Configuring rez, writing or installing plugins |

## Structure

```
rez-skills/                            # plugin root
├── .claude-plugin/
│   ├── plugin.json                    # plugin manifest
│   └── marketplace.json               # marketplace catalog
├── scripts/
│   └── install_codex.py               # Codex CLI installer / uninstaller
└── skills/
    ├── README.md                      # This file
    ├── rez-core-concepts/SKILL.md
    ├── rez-package-definition/SKILL.md
    ├── rez-package-commands/SKILL.md
    ├── rez-resolve/SKILL.md
    ├── rez-resolve-troubleshooting/SKILL.md
    ├── rez-package-authoring/SKILL.md
    ├── rez-package-pitfalls/SKILL.md
    ├── rez-cli/SKILL.md
    └── rez-config-plugins/SKILL.md
```

Each `SKILL.md` carries YAML frontmatter with `name` and `description` only. The `name` must
match the skill's directory name — `.github/scripts/validate_plugin.py` enforces both.

## Skill Routing Guide

```
User's question:
├─ "What is rez?" / versions / requests / repositories / search path
│  → rez-core-concepts
├─ Writing or reviewing package.py / requires / variants / @early / @late
│  → rez-package-definition
├─ Version vs. variant decision / range width / authoring rules and anti-patterns
│  → rez-package-authoring
├─ "invalid syntax" on build / a value frozen on the build machine / module-scope mistakes
│  → rez-package-pitfalls
├─ commands() / env vars / PATH / PYTHONPATH / string expansion / rex
│  → rez-package-commands
├─ Resolve failed / a conflict to attribute / a package rez will not use
│  → rez-resolve-troubleshooting
├─ How the solver works / reading -v traces / solver internals
│  → rez-resolve
├─ "What is the command for…?" / build / release / test loop / flags
│  → rez-cli
├─ rezconfig / settings not applied / writing or installing a plugin
│  → rez-config-plugins
└─ Python API / ResolvedContext / suites / context bundles
   → rez-core-concepts (foundation) + the rez docs' api.rst
```

| User's question | Recommended skill |
|---|---|
| "How do rez versions sort?" | rez-core-concepts |
| "Why did I get foo-1.2 instead of foo-1.3?" | rez-resolve-troubleshooting |
| "The context failed to resolve" | rez-resolve-troubleshooting |
| "Which package pulled in this conflicting version?" | rez-resolve-troubleshooting |
| "How do I declare a build-only dependency?" | rez-package-definition |
| "Should this be a new version or a new variant?" | rez-package-authoring |
| "Why does rez-build say invalid syntax?" | rez-package-pitfalls |
| "Can I import at the top of package.py?" | rez-package-pitfalls |
| "Why does my package use the build machine's home directory?" | rez-package-pitfalls |
| "How wide should this dependency range be?" | rez-package-authoring |
| "How do I add python to PATH?" | rez-package-commands |
| "How do I build and install locally?" | rez-cli |
| "How do I release a package?" | rez-cli |
| "Where does rez look for packages?" | rez-core-concepts |
| "My setting isn't taking effect" | rez-config-plugins |
| "How do I write a shell/build-system plugin?" | rez-config-plugins |

## Install

```bash
# Agent plugin — installs every skill at once (recommended)
claude plugin marketplace add loonghao/rez-skills
claude plugin install rez@rez-skills

# Load a clone for a single session
claude --plugin-dir ./rez-skills

# Codex CLI — install this tree into a Codex skills directory
git clone https://github.com/loonghao/rez-skills.git
cd rez-skills
python3 scripts/install_codex.py              # user level: $CODEX_HOME/skills
python3 scripts/install_codex.py --project    # ./.codex/skills of the repo you run it in
python3 scripts/install_codex.py --uninstall  # remove exactly what was installed

# Via ClawHub CLI, per skill
clawhub install loonghao/rez
```

With the plugin enabled, each skill is namespaced under the plugin name, for example
`/rez:rez-resolve`.

### Codex CLI

Codex reads `<skills-dir>/<skill>/SKILL.md`, so `scripts/install_codex.py` links (POSIX) or copies
(Windows) **each skill directory individually** into `$CODEX_HOME/skills` — or `~/.codex/skills`
when `CODEX_HOME` is unset — and `--project` targets `.codex/skills` of the repository you run it
from. Both `--mode copy` and `--mode symlink` are available; the default links where possible and
falls back to copies. Re-running it is a no-op for links and refreshes copies, so `git pull` in the
clone is enough to update.

`--uninstall` removes only what the installer added and leaves your other skills alone. It is driven
by a receipt, `<skills-dir>/.rez-skills.json`; skills it does not recognise are refused at install
time unless you pass `--force`, and left in place at uninstall time.

A Codex skills directory is an **install target**, not a source: `--project` installs into *your*
repository, and this repository ships no `.codex/` snapshot. `skills/` here stays the single source
of truth.

Verify:

```bash
python3 scripts/install_codex.py --print-dest
ls "$(python3 scripts/install_codex.py --print-dest)"
codex    # then ask about rez, or run /skills
```

## CI Publishing to ClawHub

`skills/` is published to ClawHub by `.github/workflows/sync-skills.yml`.

- Pull requests touching `skills/**` or `.claude-plugin/**` first validate the plugin layout with
  `.github/scripts/validate_plugin.py --strict`, then run a **dry run** of `clawhub skill publish`
  and validate the receipt — no credentials required, no publish.
- Merges to `main` that touch `skills/**`, published releases, and manual dispatch run the real
  publish.
- The real publish requires the repository secret `CLAWHUB_TOKEN`.

The same workflow validates the Codex installer with
`.github/scripts/validate_codex_install.py --strict` on Linux and Windows: a dry run must write
nothing, an install must land a readable `SKILL.md` at every Codex entry, re-installing must change
nothing, and `--uninstall` must leave the skills directory empty.

## When Skills Activate

The skills trigger when:

- The project contains `package.py` files or a rez package repository layout
- The user mentions `rez`, `rez-env`, `rez-build`, `rez-release`, or `package.py`
- The user asks about package resolves, variants, or VFX/animation environment management

## Links

- **Rez GitHub**: https://github.com/AcademySoftwareFoundation/rez
- **Rez docs**: https://rez.readthedocs.io
- **ClawHub**: https://clawhub.ai/loonghao/rez
