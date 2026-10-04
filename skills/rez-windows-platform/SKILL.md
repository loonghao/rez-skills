---
name: rez-windows-platform
description: "Running rez on Windows — why only cmd, gitbash, powershell and pwsh are registered there and how the default is picked, the path-separator trap where cmd and pwsh join with ';' while gitbash joins with ':', backslash versus forward slash in generated shell code, the platform/arch/os implicit packages, using rez-interpret as a cross-shell oracle without launching a shell, install and config-file layout, and CI differences. Use when a package or resolve behaves differently on Windows, when porting a package across operating systems, or when a one-liner works in bash and fails in cmd. Covers Rez 3.4.0."
---

# Rez on Windows

> **One-sentence summary**: on Windows rez registers only `cmd`, `gitbash`, `powershell` and `pwsh`
> as shells, and those shells disagree about the path separator — `cmd` and `pwsh` join lists with
> `;` while `gitbash` joins them with `:` — which is why the same `package.py` can produce a working
> `PATH` in one shell and a broken one in another.

Skill scope: **the Windows-specific differences**. For the `commands()` body and the rex API in
general see `rez-package-commands`; for `package.py` attributes see `rez-package-definition`; for
config layering and plugin discovery see `rez-config-plugins`; for a resolve that fails on any
platform see `rez-resolve-troubleshooting`.

> **Platform note.** Every fenced block in this skill is tagged with the shell it belongs to
> (```powershell, ```bat, ```bash). Commands tagged `powershell` or `bat` are **Windows-only** and
> will fail on Linux or macOS, where those shells are not registered. The `rez-interpret` examples
> are safe to run anywhere — it only prints text, never launches a shell.

## Which shells exist on Windows

Rez ships shell plugins for bash, sh, csh, tcsh, zsh, cmd, gitbash, powershell and pwsh, but only the
ones valid for the current platform are registered. On Windows:

```text
cmd, gitbash, powershell, pwsh
```

The POSIX shells are **not** registered there, so `rez-env --shell bash` is not a valid value on
Windows and vice versa. Ask rez rather than assuming, because the answer is platform-dependent:

```python
from rez.shells import get_shell_types

print(get_shell_types())   # ['cmd', 'gitbash', 'powershell', 'pwsh'] on Windows
```

## How the default shell is chosen

The order is `default_shell`, then `system.shell`:

```bash
rez-config default_shell
```

`default_shell` is empty (`''`) out of the box, in which case rez falls back to detecting the current
shell — on Windows that is typically `powershell`. There is **no** `default_windows_shell` setting in
rez 3.4.0; `default_shell` is the only knob, and it applies on every platform.

To target a shell for one invocation:

```powershell
# Windows only — 'cmd' is not a registered shell on Linux or macOS
rez-env foo --shell cmd
```

```bat
REM Windows only
rez-env foo --shell cmd
```

## The path separator trap

This is the difference that breaks real packages. The same rex produces different text per shell:

```bash
rez-interpret -c 'env.MY_PATH.prepend("C:/pkgs/foo/bin")' -f cmd --pv MY_PATH
rez-interpret -c 'env.MY_PATH.prepend("C:/pkgs/foo/bin")' -f pwsh --pv MY_PATH
rez-interpret -c 'env.MY_PATH.prepend("C:/pkgs/foo/bin")' -f gitbash --pv MY_PATH
```

With `MY_PATH` already set, the generated code is:

| Shell | Generated line | Separator |
|---|---|---|
| `cmd` | `set MY_PATH=C:\pkgs\foo\bin;C:\Program Files\Git\usr\bin;...` | `;` |
| `pwsh` | `Set-Item -Path "Env:MY_PATH" -Value ("C:\pkgs\foo\bin;" + ...)` | `;` |
| `gitbash` | `export MY_PATH="C:/pkgs/foo/bin:${MY_PATH}"` | `:` |

Two things to take from it:

- **The list separator is `;` under `cmd` and `pwsh`, and `:` under `gitbash`.** A `package.py` that
  builds a list by hand — joining with `:` because it was written on Linux — produces a single
  unreadable entry on Windows. Use the rex list operations (`append`, `prepend`, `set`) and let rez
  pick the separator.
- **`cmd` and `pwsh` emit backslashes; `gitbash` keeps forward slashes.** Both work in their own
  shell, so the difference only bites when you compare or string-match paths.

The separator applies to every list-valued variable, not just `PATH` — `PYTHONPATH`,
`LD_LIBRARY_PATH`-style variables and any custom list behave the same way.

## Backslashes, drive letters and path separators

- `os.sep` is a backslash and `os.pathsep` is a semicolon on Windows, versus a forward slash and a
  colon on POSIX.
- **`packages_path` is a Python list**, so it is immune to the separator problem — do not build a
  `;`-joined string for it.
- Flags that *do* take a joined path list use `os.pathsep`, so they take `;` on Windows. This
  includes `rez-env --paths` and `rez-search --paths`:

```powershell
# Windows only — the separator is ';' here, not ':'
rez-env --paths "C:/studio/packages;C:/studio/local" foo -o context.rxt
```

- Paths inside a `package.py` should be written with forward slashes or with `{root}`, and joined
  with the rex API. A hard-coded backslash will not survive a move to Linux, and a hard-coded colon
  will not survive a move to Windows.

## The platform implicit packages

Windows resolves add implicit packages that carry the OS identity. On a Windows 11 machine:

```bash
rez-config implicit_packages
```

```text
- ~platform==windows
- ~arch==AMD64
- ~os==windows-10.0.26100.SP0
```

Three consequences:

1. Any variant that must differ per OS keys off `platform` or `os`, not off `sys.platform` at module
   scope — see `rez-package-pitfalls` for why module scope is evaluated on the build machine.
2. The `os` version string is a moving target: a Windows feature update changes `~os`, so a variant
   pinned to an exact `os` version stops resolving after an upgrade. Prefer `platform`.
3. `platform_map` (default `{}`) can normalise a platform name across machines when a fleet reports
   slightly different values.

## rez-interpret: the cross-shell oracle

`rez-interpret` executes rex and prints the resulting shell code for any registered shell. It never
launches one, which makes it the cheapest way to answer "what will this actually do on `cmd`?" —
including on a Linux build machine that has no `cmd` at all.

```bash
rez-interpret -c 'env.PATH.prepend("{root}/bin")' -f cmd
rez-interpret -c 'env.PATH.prepend("{root}/bin")' -f pwsh
rez-interpret --no-env -c 'env.FOO = "bar"' -f cmd
```

`-f` accepts `cmd`, `gitbash`, `powershell`, `pwsh`, plus `dict` and `table` for a
shell-independent view. `--pv` marks variables that should be updated rather than overwritten on
first reference — without it, a `prepend` against a variable rez cannot see looks like a plain
overwrite, which is the most common way to misread this output.

Use it when reviewing a `package.py` for cross-platform behaviour, and when a one-liner works in bash
but not in `cmd`.

## Install and config layout

Rez is installed into the Python it was pip-installed into, so on Windows the entry points land in
that interpreter's `Scripts` directory rather than a `bin` directory. Confirm which rez you are
running and where its config comes from:

```bash
rez-status
rez-config --source-list packages_path
```

`--source-list` prints the config files rez actually read, in order, with real paths. On Windows those
are drive-lettered paths such as
`C:\Users\<user>\AppData\Local\Programs\Python\Python312\Lib\site-packages\rez\rezconfig.py`. It is
the fastest way to prove a setting is coming from the file you think it is.

## CI differences

- Rez needs the Python it was installed into on `PATH`. On Windows hosted runners, prefer
  `actions/setup-python` and install rez into it, so the `Scripts` directory is already on `PATH`.
- Build and test steps are the same commands — `rez-build --install`, then `rez-test` — but the shell
  the runner uses to invoke them differs, which changes quoting. A request with a range
  (`rez-env 'foo<2'`) needs single quotes under bash and gitbash, and double quotes under `cmd`.
- Package tests that shell out should not assume a POSIX shell. See `rez-test-ci` for wiring tests
  into CI.
- `rez-benchmark` timings are not comparable across operating systems; compare Windows to Windows.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `rez-env --shell bash` rejected | `bash` is not registered on Windows; use `cmd`, `gitbash`, `powershell` or `pwsh` |
| A list variable arrives as one long entry | joined with `:` on a shell that expects `;` (or the reverse) |
| `PATH` grew instead of prepending | compared without `--pv`; the parent value rez cannot see looks like it was overwritten |
| A one-liner works in bash, fails in `cmd` | quoting differs — single quotes are not quoting in `cmd` |
| Variant stops resolving after a Windows update | the variant pins an exact `~os` version; prefer `platform` |
| Rez is not found in CI | the install's `Scripts` directory is not on `PATH` |
| Paths have the wrong slashes | `cmd`/`pwsh` emit backslashes, `gitbash` emits forward slashes; both are correct for their shell |
| A package works when built on Windows only | a module-scope value was frozen on the build machine — see `rez-package-pitfalls` |

## Agent workflow

1. Establish the platform's registered shells from rez itself (`get_shell_types`), not from memory.
2. Check `rez-config default_shell` — empty means detection picked `powershell` on Windows.
3. For any list-valued environment variable, use rex `append`/`prepend` so the separator is chosen per
   shell; never hand-join with `:` or `;`.
4. Verify generated code with `rez-interpret -f <shell> --pv <VAR>` for each shell you must support.
5. Prefer `platform` over an exact `os` version when a variant needs to differ per OS.
6. Use `rez-config --source-list <setting>` to prove which config file a value came from.
