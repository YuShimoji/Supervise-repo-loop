# Coordinator recovery lease prompt

Use this text for the heartbeat attached to the one primary Coordinator task.
It is not an idle scheduler.

```text
[COORDINATOR RECOVERY LEASE — EXACT ROUTE SET ONLY]

Operate the installed supervise-repo-loop recovery path. Require scheduler
schema v2. This wake exists only because the previous foreground pass ended
with a durable prepared delivery or repository route lease.

1. Run the deterministic coordinator-plan once.
2. If there is no prepared scheduler delivery and active_routes is empty,
   pause this automation immediately and return DONT_NOTIFY. Do not inspect
   repositories, present cards, probe blockers, request successors, or claim
   ordinary READY work.
3. If a prepared delivery exists, read only its exact recipient for its
   delivery token. Record the existing send when present; otherwise resend only
   the identical envelope and payload. Never release or regenerate its identity.
4. Build one wait_threads request from plan.wait_targets, deduplicated by exact
   host, task, and cursor. Use one immediate or bounded snapshot and process
   only the first target that completes or needs attention. If an existing
   route has no cursor, use that immediate snapshot to attach the returned
   cursor to the same token/hash lease before any checkpoint.
5. If every target is unchanged, persist no semantic state, emit no status or
   progress message, keep only the existing route leases, and return
   DONT_NOTIFY. Do not loop inside this wake.
6. If one route has a semantic result, persist it and complete only that route
   lease. Preserve every other recipient, delivery token, payload hash, and
   cursor unchanged.
7. A consumed result is a real event. From that point, continue the primary
   portfolio drain described in references/coordinator-task-prompt.md: process
   the resulting mandatory protocol handoff to its next external wait or
   terminal, fill safe capacity from other repositories, start at most one new
   unit of work per repository in the pass, then do one multi-target wait.
8. Pause this automation as soon as no prepared delivery or active route lease
   remains. Never leave an idle periodic model wake active.

This lease never owns an independent status calculation. The primary Prompt
owns the canonical portfolio index and user-facing semantic transition output.
Do not infer commit, push, release, publication, rights, or acceptance
authority.
```
