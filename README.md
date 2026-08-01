# supervise-repo-loop

This repository is the version-controlled source for the static
`supervise-repo-loop` skill. Codex loads the installed runtime copy from
`C:\Users\thank\.codex\skills\supervise-repo-loop`; that installed directory
also owns the live Coordinator state.

The two surfaces have deliberately different responsibilities:

- this repository: skill instructions, protocol and design documents, schemas,
  deterministic code, and tests;
- installed skill: a verified copy of those static files plus the only live
  `state/` used by the Coordinator.

Do not copy live state into this repository or replace the installed skill with
a directory junction. See [development.md](docs/development.md) for the test,
verification, and non-destructive installation workflow.

## Another computer

Git carries the static scheduler implementation, tests, schemas, and
automation contracts. It deliberately does not carry the installed `state/`,
Codex automation records, a primary Coordinator task ID, or host-local
maintenance gates. On a new computer, install the static skill and bind a new
paused recovery heartbeat to that computer's exact primary Coordinator; never
reuse an old task ID.

Run the read-only portability check after cloning:

```powershell
.\scripts\verify-portable-checkout.ps1
```

See [host-portability.md](docs/host-portability.md) for the complete transfer
and Codex task-scheduler boundary.

## Quick verification

```powershell
.\scripts\test.ps1
```

Read-only frontier audit:

```powershell
python scripts/supervise_repo_loop.py frontier-audit --dry-run
```

The audit reports legacy-unverified or stale artifact frontiers without
writing live state. See
[frontier-reconciliation.md](docs/frontier-reconciliation.md).

Read-only project-context audit:

```powershell
python scripts/supervise_repo_loop.py project-context-audit --dry-run
```

This second gate verifies the project north star, roadmap position, all active
lane frontiers, decisions, and evidence coverage before any ordinary
Supervisor/Worker route. See
[project-context-frontier.md](docs/project-context-frontier.md).

## Preview or install static changes

```powershell
.\scripts\sync-installed-skill.ps1 -WhatIf
.\scripts\sync-installed-skill.ps1
```

The installer never deletes target files and never reads from or writes to the
installed `state/` or `.serena/` trees.
