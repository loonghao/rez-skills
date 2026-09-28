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

Individual skills are also published to [ClawHub](https://clawhub.ai/loonghao/rez):

```bash
clawhub install loonghao/rez
```

## What's inside

Skills under [`skills/`](skills/) — all of them load together when the plugin is enabled:

| Skill | Best for |
|-------|----------|
| [`rez-core-concepts`](skills/rez-core-concepts/SKILL.md) | Packages, versions, requests, repositories, search path, variants, ephemerals |
| [`rez-package-definition`](skills/rez-package-definition/SKILL.md) | Authoring `package.py` — attributes, `@early`/`@late`, `requires`, variants |
| [`rez-package-commands`](skills/rez-package-commands/SKILL.md) | The `commands()` section and the rex environment API |
| [`rez-resolve`](skills/rez-resolve/SKILL.md) | Solver internals, `-v` debug output, diagnosing conflicts and cycles |
| [`rez-cli`](skills/rez-cli/SKILL.md) | `rez-env`, `rez-build`, `rez-release`, `rez-context` and the rest, with flags |
| [`rez-config-plugins`](skills/rez-config-plugins/SKILL.md) | Configuration layering, key settings, plugin types and discovery |

See the [skills README](skills/README.md) for the routing guide and authoring principles.

## Layout

The repository root is the plugin root, which keeps `skills/` as the single source of truth for
both distribution paths:

```
rez-skills/                        # plugin root
├── .claude-plugin/
│   ├── plugin.json                # plugin manifest
│   └── marketplace.json           # marketplace catalog, so `plugin install` works
└── skills/
    └── <skill>/SKILL.md           # one directory per skill
```

Because `skills/` stays where it is, `.github/workflows/sync-skills.yml` keeps discovering and
publishing each skill directory exactly as before.

## Validation

```bash
python3 .github/scripts/validate_plugin.py --strict
```

The script checks the plugin and marketplace manifests and every skill directory under `skills/`.

## Publishing

`.github/workflows/sync-skills.yml` publishes every directory under `skills/` to
[ClawHub](https://clawhub.ai/loonghao/rez):

- **Pull requests** touching `skills/**` or `.claude-plugin/**` validate the plugin layout, then run
  `clawhub skill publish --dry-run` for each skill and validate the receipt. No credentials needed,
  nothing is published.
- **Merges to `main`** touching `skills/**`, published releases, and manual dispatch run the real
  publish, which requires the `CLAWHUB_TOKEN` repository secret.

The ClawHub CLI is pinned to `0.23.3`.

## Source

Content is derived from the Rez 3.4.0 repository — `docs/source/*.rst` and `src/rez/**`.

## License

MIT — see [LICENSE](LICENSE).
