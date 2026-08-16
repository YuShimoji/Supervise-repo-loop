# Supervise Repo Loop compatibility entry

The old v2 scheduler topology was retired on 2026-08-17. This repository no longer owns a live scheduler, portfolio queue, route lease, Supervisor binding, or persistent Worker state.

The current development path is deliberately small:

```text
User
  -> Coordinator (one decision)
  -> Worker (one bounded probe)
  -> Coordinator (records evidence and chooses the next probe)
  -> FrontierBoard (durable user-visible frontier)
```

| Surface | Canonical source | Purpose |
| --- | --- | --- |
| Core and setup | `YuShimoji/project-reflection-coordinator` | Versioned Core, host profile, dependency lock, installation and runtime sync |
| Durable Board | `YuShimoji/FrontierBoard` | Cards, choices, evidence, project frontier and local app data |
| This repository | `YuShimoji/Supervise-repo-loop` | Backward-compatible discovery and clean-host bootstrap only |

## Planner007 setup

From a clean checkout of this repository:

```powershell
pwsh -NoProfile -File .\scripts\bootstrap-frontier-loop.ps1 `
  -StorageRoot 'C:\Users\thank\Storage' `
  -Role standby
```

The bootstrap performs a normal `git fetch --prune` and `pull --ff-only`, verifies that the canonical minimum revision is in history, and invokes the Coordinator repository's own `setup-remote-coordinator.ps1`. That setup locks, tests, builds, canary-checks, and installs FrontierBoard before it installs the Core and thin skills. Dirty, ahead, divergent, or incorrectly bound checkouts are preserved and held.

No content project is resumed by setup. The six music/video projects restart only after an explicit fresh ranking operation.

The retired implementation remains recoverable from Git commit `c82b88f80c8e595d2ff6303c65bf54aadab15035`; it is history, not runtime authority.

