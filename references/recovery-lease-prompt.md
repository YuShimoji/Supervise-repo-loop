# Coordinator recovery lease prompt

Use this text for the heartbeat attached to the one primary Coordinator task.
It is not an idle scheduler.

```text
[COORDINATOR RECOVERY LEASE — EXACT ROUTE SET ONLY]

Operate the installed supervise-repo-loop recovery path. Require scheduler
schema v2. This wake exists only because the previous foreground pass ended
with a durable prepared delivery, repository route lease, or a recovery-owned
typed runtime phase.

Before step 1, verify that this wake is attached to the exact active primary
Coordinator task, the process CODEX_THREAD_ID equals
coordinator_state.coordinator_task.task_id, and every live-state argument is
the canonical path under the installed skill state directory. This automation
has no writer identity of its own: it may wake the bound primary but may not
claim as an automation, repair, audit, or substitute task. On any identity or
path mismatch, make no live mutation and return DONT_NOTIFY. Never copy the
primary ID or perform a primary rebind; rebinding is a separate explicit idle-
only administrator operation.

1. Run the deterministic coordinator-plan once.
2. Use plan.watchdog_should_be_armed as the only arm gate. If it is false,
   pause this automation immediately and return DONT_NOTIFY. Do not inspect
   repositories, present cards, probe blockers, request successors, or claim
   ordinary READY work. An empty active_routes array does not permit a pause
   while an authorized runtime ledger is in EFFECT_INTENT, EFFECT_PREPARED,
   REPAIR_PREPARED, ROLLBACK_REQUIRED, or RESULT_READY.
3. If a prepared delivery exists, read only its exact recipient for its
   delivery token. Record the existing send when present; otherwise resend only
   the identical envelope and payload. Never release or regenerate its identity.
4. Observe routes with their persisted transport. Poll every ChatGPT
   Supervisor in plan.poll_targets exactly once with read_thread; never pass a
   ChatGPT chat ID to wait_threads. Build one wait_threads request only from
   the Codex Worker entries in plan.wait_targets, deduplicated by exact host,
   task, and cursor. Consume a semantic Supervisor result before waiting;
   otherwise use exactly one immediate or bounded Worker snapshot and process
   only the first target that completes or needs attention. If an existing
   route has no cursor, use the matching transport observer to attach the
   returned cursor to the same token/hash lease before any checkpoint.
5. If every target is unchanged, persist no semantic state, emit no status or
   progress message, keep only the existing route leases, and return
   DONT_NOTIFY. Do not loop inside this wake.
6. If one route has a semantic result, use
   coordinator-action-apply-result with the current independently collected
   authority signal. The write-ahead reducer must validate the exact action,
   recipient, result, frontier epoch, project-context revision,
   Git/authority observation, and Mission; then apply both compare-and-swap
   gates, update frontier, Mission, scheduler, and portfolio v4, and close
   only that route. A delivery token, cursor, ACK, or non-empty evidence string
   is not a semantic result. Preserve every other recipient, delivery token,
   payload hash, and cursor unchanged.
7. A consumed result is a real event. From that point, continue the primary
   portfolio drain described in references/coordinator-task-prompt.md: process
   the resulting mandatory protocol handoff to its next external wait or
   terminal, fill safe capacity from other repositories, start at most one new
   unit of work per repository in the pass, then do one multi-target wait.
8. Before any state-changing checkpoint, rebuild canonical portfolio JSON v4
   from the same scheduler, frontier, and project-context revisions, including the exact
   structured active route set and FrontierCertificates, then run
   `portfolio-render`. Scheduler/frontier revision, capacity, route count,
   action, recipient, token, cursor, status, authority fingerprint, artifact,
   branch, or HEAD mismatch forbids the checkpoint.
9. Pause this automation as soon as a rebuilt plan reports
   watchdog_should_be_armed=false. Never leave an idle periodic model wake
   active.
10. A typed runtime ledger is recovery-owned from EFFECT_INTENT until COMPLETE.
    Resume only its exact effect ledger and allowlisted handler; require its
    receipt before completion and never release or replace it with an ordinary
    local action. AUTHORIZED alone is still releasable and does not arm a
    periodic wake because no effect intent has been persisted.

This lease never owns an independent status calculation. The primary Prompt
owns the canonical portfolio index and user-facing semantic transition output.
The CODEX_THREAD_ID check is a cooperative same-user safety fence, not a
malicious-process security boundary.
Do not infer commit, push, release, publication, rights, or acceptance
authority.
```
