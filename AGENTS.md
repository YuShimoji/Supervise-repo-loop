# Compatibility bootstrap repository rules

This repository is a stable compatibility entry for the simplified Frontier loop. The portable source of truth is `YuShimoji/project-reflection-coordinator`; the UI dependency is `YuShimoji/FrontierBoard`.

- Do not reintroduce the retired global Coordinator / Web Supervisor / persistent Worker scheduler.
- Do not store host IDs, task IDs, credentials, cursors, local state, absolute profile paths, generated media, or installed runtime state.
- `scripts/bootstrap-frontier-loop.ps1` may only clone or fast-forward the canonical Coordinator checkout and then delegate to its checked-in setup script.
- Never reset, clean, stash, rebase, force-push, or overwrite a dirty checkout.
- Keep `config/canonical-source.json` anchored to a tested minimum Coordinator revision. Later descendants of that revision are allowed so future accepted updates can flow to another host.
- Run `pwsh -NoProfile -File tests/Test-BootstrapContract.ps1` before publishing.

