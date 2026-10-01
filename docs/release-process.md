# Release process

This repository ships through a single automated pipeline, owned by two workflows.

## How a release happens

1. **`.github/workflows/release-please.yml`** runs on every push to `main`. It reads the
   conventional commits that landed since the last release and opens — or updates — one release
   pull request titled `chore(main): release <version>`.
2. Merging that release pull request is what produces the tag. The merge itself only lands the
   version bump; the next run of the same workflow creates the `v<version>` tag and the GitHub
   Release. The `extra-files` entry in `release-please-config.json` moves
   `.claude-plugin/plugin.json` to the same version, so the shipped plugin never reports a version
   older than the tag it was cut from.
3. **`.github/workflows/sync-skills.yml`** publishes the skills to ClawHub. For a release the
   entry point is the final step of `release-please.yml`, which dispatches this workflow with
   `gh workflow run sync-skills.yml --repo "$GITHUB_REPOSITORY" --ref main`. The `release: published`
   trigger that `sync-skills.yml` also declares cannot fire here: release-please creates the release
   with `GITHUB_TOKEN`, and GitHub does not start a workflow for an event created with that token,
   so nothing would ever publish if the dispatch step were removed. It also runs on pushes to `main`
   that touch `skills/**`, `plugin.json`, `.claude-plugin/**`, the schemas, the validation scripts,
   or `scripts/install_codex.py`.

On `pull_request` the publish step always runs with `--dry-run`, so no pull request can publish on
its own. The first real publish happens with the first GitHub Release.

## Why the first release is pinned to v1.0.0

`.release-please-manifest.json` holds the last version release-please considers released. It is the
source of truth for the next bump — release-please reads this file, not the tags.

The manifest was seeded to `1.0.0` in `adc059f5`, before any tag existed. Two consequences follow
from that, and they are the reason releases looked like they were not running:

- the plugin and the ClawHub skills already report `1.0.0` while the repository has no `v1.0.0`
  tag;
- release-please treats `1.0.0` as already released, so it would tag the first release `v1.1.0` and
  leave `v1.0.0` unreachable forever.

The first release is therefore pinned to `v1.0.0` with a `Release-As: 1.0.0` trailer on the commit
that adds this file. The trailer forces the version for the release that contains it and has no
effect on any later release, so it needs no cleanup afterwards.

Do not lower the manifest baseline instead of pinning. Under the `simple` release type a pre-1.0
`feat` maps to a minor bump, so a `0.9.x` baseline resolves to `v0.10.0`, not `v1.0.0`.

## Manual recovery

Release-please only reaches a tag on a later run. When a run fails for an environment reason — an
unset repository permission, a registry outage — it stays failed until something pushes to `main`
again, which is why both workflows also accept `workflow_dispatch`:

```bash
gh workflow run release-please.yml --ref main
```

Two setup requirements are easy to miss:

- The `Release Please` workflow needs *Settings → Actions → General → Workflow permissions →
  Allow GitHub Actions to create and approve pull requests*. Without it, release-please builds the
  release commit, fails to open the pull request, and no tag is ever produced.
- Release pull requests are opened by the `github-actions` app, so GitHub holds their workflow runs
  for approval. Approve the run, or the release pull request reports no checks.
