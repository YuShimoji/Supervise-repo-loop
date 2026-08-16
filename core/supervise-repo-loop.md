---
name: frontier-evidence-loop-bootstrap
version: 10.2-frontier.1
status: compatibility-bootstrap
deployment_ready: false
canonical_repository: YuShimoji/project-reflection-coordinator
canonical_revision_floor: 886deb2b3593070f9e6d0918c373af41004e181f
canonical_core_sha256: BB6CC6421676E4DFB61C67197ECA7C760C45F666E9143CE6BA7AADA579DA5E28
---

# Simplified Frontier loop compatibility contract

This file proves that the old public entry has moved to the simplified architecture. It is not installed directly; the byte-authoritative Core is `core/supervise-repo-loop.md` in the canonical Coordinator repository.

```text
Coordinator
  -> Worker(PROBE)
  -> Coordinator
  -> FrontierBoard
```

- Coordinator selects one frontier decision and constructs one bounded probe.
- Worker receives only the context needed for that probe and returns evidence.
- Coordinator records the evidence and chooses the next probe, a user decision card, or a concrete stop.
- FrontierBoard owns durable user-visible cards and frontier state; Git repositories do not store host task identities or Board data.
- Project lanes do not wait for portfolio-wide cycles.
- Setup never resumes content work automatically. A fresh explicit ranking operation is required.

The compatibility bootstrap accepts the pinned Coordinator revision or a descendant on `main`, then delegates all validation and installation to that checkout. A dirty, ahead, divergent, detached, wrongly bound, or unverifiable checkout is preserved and held. Reset, stash, clean, rebase, forced history, and legacy-state restoration are forbidden.
