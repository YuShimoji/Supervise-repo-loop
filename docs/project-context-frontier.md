# Project context frontier

## Guarantee

Every ordinary Supervisor or Worker route must carry one
`SupervisorContextEnvelope` built from the exact current repository state. The
envelope is not a chat summary. It binds the project north star, roadmap
position, bottleneck, completion definition, every active lane frontier,
decisions since the prior context event, included and omitted evidence, retired
artifacts, current authority fingerprint, and the action being requested.

This project-wide gate sits above the per-lane artifact frontier. A current
artifact in one lane cannot prove that the Supervisor still has the full
project picture. Both gates must certify before ordinary work continues.

The static repository contains only the implementation and schemas. The
installed skill owns the only live `state/project-context-ledger.v1.json`.
Development, audit, and repair tasks must not initialize or copy that live
ledger.

## Monotonic context reducer

Each `ProjectContextRecord` is an append-only compare-and-swap event:

- `project_context_revision` is exactly the prior revision plus one;
- every active lane names its exact current `frontier_event_id`;
- the authority fingerprint equals an independent current observation;
- replacement explicitly supersedes the prior context event;
- source precedence is `human > supervisor > worker > repo_observation > coordinator`;
- unresolved cross-lane conflicts prevent certification;
- exact replay is idempotent and conflicting replay fails closed.

No context is inferred from chat recency, a portfolio row, an old handoff, a
filename, or the most recently delivered result. An existing or newly
registered project without a record is `legacy_unverified` and enters
`CONTEXT_RECONCILIATION_REQUIRED`.

## Semantic invalidation triggers

Context reconciliation is triggered by meaning-bearing change, not by an idle
heartbeat. A new record is required when any of these changes:

- registration or active-lane topology;
- a lane's frontier event, artifact disposition, or retirement lineage;
- authority revision or complete authority fingerprint;
- north star, roadmap position, bottleneck, completion definition, or next gate;
- a decision, cross-lane conflict, evidence inclusion, or evidence omission.

Unchanged waits and periodic recovery wakes do not manufacture a new context
revision.

## Action and result binding

`coordinator-plan` first emits `reconcile_repository_frontier` when a lane
frontier is not certified, then `reconcile_project_context` when the project
view is absent or stale. Ordinary actions are admitted only after both gates
pass. Their action identity includes the complete `SupervisorContextEnvelope`,
so the envelope must accompany the exact send to the Supervisor or Worker.

An external result for a context-bound action must echo
`supervisor_context_envelope_id` and
`based_on_project_context_revision`. Application performs two current-state
checks:

1. project context revision/envelope compare-and-swap;
2. lane artifact frontier epoch compare-and-swap.

If either changed while the route was in flight, the result is closed as a
terminal quarantine and cannot update the Mission, artifact frontier, or
current project context. The exact result remains auditable.

An authority change caused by the authorized route itself is not mistaken for
pre-existing context drift. The result must still match the Coordinator's new
independent authority observation; after its frontier event is applied, the old
context record becomes reconciliation-required before another ordinary action.

## Reconciliation and audit

The primary Coordinator applies a prepared context event only through:

```powershell
python scripts/supervise_repo_loop.py project-context-apply-event --event <event.json>
```

The command independently verifies authority and every active lane frontier.
Only the exact bound primary Coordinator and canonical installed state path may
write live state.

Any task may run the read-only audit:

```powershell
python scripts/supervise_repo_loop.py project-context-audit --dry-run
```

The audit describes missing or stale context and the evidence required for a
reconciliation event. It does not create that event, select a Mission, present
a review artifact, or mutate any repository.

## Generic canary contract

The gate is keyed only by normalized repository identity, lane identity, exact
authority, and typed events. Product names and content domains never appear in
the runtime decision rules. Acceptance covers four existing-project migration
shapes plus three newly registered shapes—web product, game runtime, and media
pipeline—through the same schema, reducer, plan gate, and envelope contract.
