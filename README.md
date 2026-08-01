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

## Quick verification

```powershell
.\scripts\test.ps1
```

## Preview or install static changes

```powershell
.\scripts\sync-installed-skill.ps1 -WhatIf
.\scripts\sync-installed-skill.ps1
```

The installer never deletes target files and never reads from or writes to the
installed `state/` or `.serena/` trees.

