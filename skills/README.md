# rez — AI Agent Skills

Agent-friendly skills for **[Rez](https://github.com/AcademySoftwareFoundation/rez)** (3.4.0), the
package and environment manager used in VFX/animation pipelines.

Source material: the Rez repository — `docs/source/*.rst` and `src/rez/**` (notably
`solver.py`, `rezconfig.py`, `rex.py`, `build_system.py`, `cli/_util.py`).

`skills/` is the **canonical source**. Project-level agent directories (`.agents/`, `.claude/`,
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
| **rez-resolve** | Solver internals, `-v` debug output, conflicts/cycles/reductions, graphs, caching | Resolve failures, unexpected versions or variants |
| **rez-cli** | Command reference — `rez-env`, `rez-build`, `rez-release`, `rez-context`, `rez-search`, flags | "What is the command for…", build/release/test loops |
| **rez-config-plugins** | Config layering, merge rules, key settings, the seven plugin types and discovery | Configuring rez, writing or installing plugins |

## Structure

```
skills/
├── README.md                          # This file
├── rez-core-concepts/SKILL.md
├── rez-package-definition/SKILL.md
├── rez-package-commands/SKILL.md
├── rez-resolve/SKILL.md
├── rez-cli/SKILL.md
└── rez-config-plugins/SKILL.md
```

Each `SKILL.md` carries YAML frontmatter with `name` and `description` only.

## Skill Routing Guide

```
User's question:
├─ "What is rez?" / versions / requests / repositories / search path
│  → rez-core-concepts
├─ Writing or reviewing package.py / requires / variants / @early / @late
│  → rez-package-definition
├─ commands() / env vars / PATH / PYTHONPATH / string expansion / rex
│  → rez-package-commands
├─ Resolve failed / wrong version / unexpected variant / conflict
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
| "Why did I get foo-1.2 instead of foo-1.3?" | rez-resolve |
| "The context failed to resolve" | rez-resolve |
| "How do I declare a build-only dependency?" | rez-package-definition |
| "How do I add python to PATH?" | rez-package-commands |
| "How do I build and install locally?" | rez-cli |
| "How do I release a package?" | rez-cli |
| "Where does rez look for packages?" | rez-core-concepts |
| "My setting isn't taking effect" | rez-config-plugins |
| "How do I write a shell/build-system plugin?" | rez-config-plugins |

## Install

```bash
# Via ClawHub CLI
clawhub install loonghao/rez

# Or copy skills/ into your AI agent's skills directory
```

## CI Publishing to ClawHub

`skills/` is published to ClawHub by `.github/workflows/sync-skills.yml`.

- Pull requests touching `skills/**` run a **dry run** of `clawhub skill publish` and validate the
  receipt — no credentials required, no publish.
- Merges to `main` that touch `skills/**`, published releases, and manual dispatch run the real
  publish.
- The real publish requires the repository secret `CLAWHUB_TOKEN`.

## When Skills Activate

The skills trigger when:

- The project contains `package.py` files or a rez package repository layout
- The user mentions `rez`, `rez-env`, `rez-build`, `rez-release`, or `package.py`
- The user asks about package resolves, variants, or VFX/animation environment management

## Links

- **Rez GitHub**: https://github.com/AcademySoftwareFoundation/rez
- **Rez docs**: https://rez.readthedocs.io
- **ClawHub**: https://clawhub.ai/loonghao/rez
