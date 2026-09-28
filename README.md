# rez-skills

Agent-friendly skills for [Rez](https://github.com/AcademySoftwareFoundation/rez) — the package and
environment manager used in VFX and animation pipelines.

These skills let AI agents work with Rez accurately instead of guessing: correct version and request
semantics, correct `package.py` structure, and a concrete triage path when a resolve fails.

## What's inside

Six skills under [`skills/`](skills/):

| Skill | Best for |
|-------|----------|
| [`rez-core-concepts`](skills/rez-core-concepts/SKILL.md) | Packages, versions, requests, repositories, search path, variants, ephemerals |
| [`rez-package-definition`](skills/rez-package-definition/SKILL.md) | Authoring `package.py` — attributes, `@early`/`@late`, `requires`, variants |
| [`rez-package-commands`](skills/rez-package-commands/SKILL.md) | The `commands()` section and the rex environment API |
| [`rez-resolve`](skills/rez-resolve/SKILL.md) | Solver internals, `-v` debug output, diagnosing conflicts and cycles |
| [`rez-cli`](skills/rez-cli/SKILL.md) | `rez-env`, `rez-build`, `rez-release`, `rez-context` and the rest, with flags |
| [`rez-config-plugins`](skills/rez-config-plugins/SKILL.md) | Configuration layering, key settings, plugin types and discovery |

See the [skills README](skills/README.md) for the routing guide and authoring principles.

## Install

```bash
clawhub install loonghao/rez
```

Or copy `skills/` into your agent's skills directory.

## Publishing

`.github/workflows/sync-skills.yml` publishes every directory under `skills/` to
[ClawHub](https://clawhub.ai/loonghao/rez):

- **Pull requests** touching `skills/**` run `clawhub skill publish --dry-run` for each skill and
  validate the receipt. No credentials needed, nothing is published.
- **Merges to `main`** touching `skills/**`, published releases, and manual dispatch run the real
  publish, which requires the `CLAWHUB_TOKEN` repository secret.

The ClawHub CLI is pinned to `0.23.3`.

## Source

Content is derived from the Rez 3.4.0 repository — `docs/source/*.rst` and `src/rez/**`.

## License

MIT — see [LICENSE](LICENSE).
