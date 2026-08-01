# Development and installation

## Source and runtime boundary

| Surface | Role | Authority |
| --- | --- | --- |
| This Git repository | Static source and test surface | Canonical for `SKILL.md`, `AGENTS.md`, `README.md`, `agents/`, `docs/`, `references/`, `schemas/`, `scripts/`, and `tests/` |
| `C:\Users\thank\.codex\skills\supervise-repo-loop` | Installed Codex skill | Runtime copy of the allowlisted static files |
| Installed `state/` | Coordinator registries, claims, events, Mission evidence, and delivery receipts | Sole live state; never source-controlled or synchronized |
| Either `.serena/` | Local indexing and editor metadata | Local-only; never synchronized by the installer |

Develop static behavior in this repository and install it in one direction.
Do not edit both copies independently. Do not move the installed directory or
replace it with a junction while a Coordinator route may be active.

## Runtime requirement

The implementation and tests use only the Python standard library. Do not
install Python packages or create a project virtual environment.

`scripts/test.ps1` resolves a usable existing interpreter in this order:

1. `-PythonPath`, for a Python executable returned by the Codex workspace
   dependency loader;
2. an already installed `uv` and an already available Python managed or found
   by it, with offline mode enforced;
3. an existing Python executable already exposed by the host.

It rejects the Windows Store placeholder and fails closed if no Python 3.11 or
newer interpreter is available. It never asks `uv` to download a runtime.

## Test

From the repository root:

```powershell
.\scripts\test.ps1
```

To use a Python executable supplied by Codex explicitly:

```powershell
.\scripts\test.ps1 -PythonPath 'C:\path\reported\by\Codex\python.exe'
```

The equivalent direct command for a host with the current cached uv runtime is:

```powershell
uv run --offline --no-project --python 3.13 python -B -m unittest discover -s tests -v
```

## Install static changes

Preview the exact destination and allowlisted files first:

```powershell
.\scripts\sync-installed-skill.ps1 -WhatIf
```

Install after the preview:

```powershell
.\scripts\sync-installed-skill.ps1
```

The synchronization command enforces this sequence:

1. run the complete source test suite;
2. enumerate only the explicit static allowlist;
3. copy those files without pruning or mirroring the destination;
4. compare every source and installed file by SHA-256;
5. run the complete suite against the installed copy.

`state/` and `.serena/` are forbidden paths even if someone later adds them to
the source tree. The command contains no removal operation, does not reverse
sync, and does not install a runtime or package.

## Activating an updated prompt

Static parity does not retroactively alter instructions already loaded into an
active Coordinator turn. At a safe route checkpoint, explicitly instruct the
same Coordinator task to reread the installed `SKILL.md`, referenced protocol
documents, and `references/coordinator-task-prompt.md`. Do not reset its live
state to force a reload.

## Cross-host development

Use [host-portability.md](host-portability.md) when moving development to
another computer. The repository is the portable source; the installed
`state/`, Codex automation IDs, primary task binding, local gate/helper paths,
and automation checkpoints are host-local. They must not be copied through
Git or inferred from an old machine.

After cloning, run the read-only host check before installation:

```powershell
.\scripts\verify-portable-checkout.ps1
```

After static installation and after the branch has been pushed, use the
stronger gate:

```powershell
.\scripts\verify-portable-checkout.ps1 -RequireInstalledParity -RequireClean -VerifyRemoteTip
```

The second command proves only source/install parity and exact branch
reflection. It does not prove that a recovery heartbeat is correctly bound or
that a live Coordinator route can move between hosts.
