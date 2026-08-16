---
name: supervise-repo-loop
description: Compatibility entry for installing or locating the simplified Coordinator plus FrontierBoard development loop. Use when an older prompt invokes $supervise-repo-loop or when a new host needs the canonical loop bootstrapped. Do not revive the retired v2 scheduler.
---

# Supervise Repo Loop compatibility entry

This repository is not the runtime authority.

1. If the canonical `run-supervise-repo-loop` skill and its verified FrontierBoard receipt are installed, read that installed skill completely and follow it.
2. Otherwise run `scripts/bootstrap-frontier-loop.ps1` from this checkout with the host's real Storage root and role.
3. After bootstrap, read the installed `run-supervise-repo-loop` skill and enter through the canonical Coordinator checkout reported by the bootstrap receipt.

The normal loop is one Coordinator decision followed by one bounded Worker `PROBE`, then evidence is returned to the same Coordinator and persisted in FrontierBoard. Do not create or reconstruct the retired multi-layer scheduler, portfolio queue, route/recovery lease, Web Supervisor adjudication chain, or persistent Worker bindings from repository history.

Bootstrap and runtime setup do not authorize content generation, upload, creative acceptance, publication, deployment, destructive cleanup, or automatic resumption of paused projects.
