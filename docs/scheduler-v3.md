# Portfolio route-lease scheduler

## Decision

Normal Coordinator operation is event driven. A recurring Codex heartbeat is
not a scheduler because every scheduled wake starts a model turn before any
repository predicate can run. The recovery automation therefore remains
paused while idle and during the primary foreground wait. It is armed only
after a bounded foreground pass checkpoints with exact route leases that need
crash or late-result recovery.

The three independent runtime axes are:

- Coordinator availability: `AVAILABLE`;
- execution: `READY | DRAINING | WAITING_USER | WAITING_EXTERNAL | IDLE`;
- recovery automation: `ARMED | PAUSED`.

`AVAILABLE` does not mean work is in flight. `READY` means a deterministic
action exists but has not been claimed; only `DRAINING` means the Coordinator
has begun an exact action or is waiting on an exact route. `IDLE` is a quiet
checkpoint, not Coordinator completion.

## User-facing transition ownership

The primary Coordinator task, not the recovery automation, owns user-facing
state transitions. `READY` is presented as `READY_UNCLAIMED` only when a real
execution or turn ceiling prevents the primary task from claiming it; it is
never a blocker or an ordinary checkpoint.

After a new Mission terminal, changed blocker, new user card, legitimate idle
checkpoint, or forced interruption, the primary Coordinator presents one
semantic transition map containing:

- `current_work`, with clickable absolute artifact/evidence, Worker Report,
  and Supervisor verdict entrypoints;
- `blockers`, limited to true `BLOCKED` items with purpose, effect,
  requirements, state, owner, origin event, qualifying/non-qualifying examples,
  completed diagnostics, input route, and next permitted probe;
- `user_actions`, limited to active `USER_DECISION` and `USER_ACTION` cards and
  explicitly `none` when no reply is required;
- `resume_anchor`, with the exact action identity/recipient or exact external
  recovery condition.

The map derives from the same deterministic plan and persisted Mission
evidence and is rendered as the same seven-stage Mermaid graph in both the
response and durable index. It is not a second scheduling function. Identical
semantic state and action/terminal identity must not repeat a map. Unchanged
recovery wakes remain silent.

The map also carries the scheduler revision and exact structured active route
set. Any difference in revision, capacity, count, repository, action,
recipient, token, cursor, or route status makes the checkpoint invalid. Route
admission and release are semantic changes even if repository artifacts do not
change.

## Incident evidence

The pre-v3 Coordinator session telemetry contained 386 heartbeat turns. Summing
each heartbeat turn's recorded `last_token_usage.total_tokens` entries gives
123,926,621 processed tokens, including cached input and intermediate model
calls. The final three unchanged heartbeats alone processed 520,395 tokens and
started about 90 seconds apart, although every final message reported no new
work or question. These figures describe model-context processing, not a direct
billing calculation, but they prove that a quiet final response was not a
zero-cost predicate check.

The old run was paused before this design was installed. The target Coordinator
session has not changed since its final heartbeat. This is why an idle Codex
automation cannot be retained as the normal sensor even when its prompt says
`DONT_NOTIFY`.

## Sensor, decision, executor separation

The scheduler has three boundaries:

1. allowlisted sensors load durable Mission state, bindings, exact task
   observations, user queues, and content hashes for registered authority
   files;
2. `build_coordinator_plan(...)` is the single deterministic decision function;
3. the Coordinator claims one returned action before presentation, state
   mutation, or external delivery; after an external receipt it moves the
   action to a repository-scoped route lease and releases the scheduler claim.

Both `coordinator-status` and `coordinator-plan` use the same decision. Generic
next-action resolution also returns the same scheduler action and action ID.
There is no independent status-only continuation test.

The plan exposes:

- semantic state fingerprint;
- one exact next action and stable action ID;
- every ready action that remains eligible;
- the short-lived scheduler claim;
- every active repository route lease and exact wait target;
- concurrency capacity and ready action count;
- `checkpoint_after_wait_allowed`, which reports structural foreground-handoff
  eligibility when durable routes remain and no safe READY action is claimable;
  it is not a wait receipt, so the executor may use it only after its bounded
  wait actually finishes unchanged;
- individual next user card;
- continue/checkpoint decision;
- watchdog arm/pause decision.

Observation timestamps and file mtimes are not semantic inputs. Registered
authority files use their content hashes.

## Action lifecycle

The durable state is `state/coordinator-scheduler.v1.json`; schema version 2
stores a short-lived `scheduler_claim`, `route_leases`, and a default external
route capacity of three. The filename remains stable so an installed v1 state
can migrate atomically without changing the runtime pointer.

The generic scheduler accepts a configured capacity from one through eight;
the current primary Coordinator contract requires three. The per-repository
limit applies to execution routes. Control-plane questions and direction
routing do not masquerade as Mission execution leases.

```text
ready action
→ scheduler claim
→ prepare durable delivery envelope
→ exact send receipt / wait cursor
→ repository route lease + scheduler release
→ persisted result or presentation evidence
→ complete that exact lease
```

Use:

```text
coordinator-plan
coordinator-action-claim --action-id ...
coordinator-action-prepare --action-id ... --recipient-thread-id ... --packet-sha256 ...
coordinator-action-sent --action-id ... --recipient-thread-id ...
coordinator-action-complete --action-id ... --outcome ...
```

If work fails before a delivery envelope is prepared, use
`coordinator-action-release`; this clears the unprepared scheduler claim
without consuming the action identity. A `prepared` scheduler claim or
`sent`/`waiting` route lease must never be released into a retry because
transport may already have occurred; reconcile its exact delivery token,
recipient, and cursor instead. Never mark a retryable failure complete.

An outbound route is in flight only after the exact recipient is persisted as
`sent` or `waiting`, together with the packet SHA-256. External-result actions
cannot complete before this receipt and require result evidence when completed.
A local `WORK_ORDER_RECEIVED` transition is ready work,
not in-flight work. Historical v2 outbound Mission states remain readable as
legacy inferred routes, but all new sends use a claim receipt.

Completed semantic action IDs are retained durably. This prevents an old
review card, unchanged blocker, or consumed successor intent from reappearing
after long uptime.

Every external message carries the prepared delivery envelope containing
`action_id`, `delivery_token`, and the payload SHA-256. Preparation is persisted
before transport. If interruption leaves a claim at `prepared`, recovery first
reads the exact recipient for that delivery token; it records the existing send
when found, otherwise it resends the identical envelope. Supervisor and Worker
routes treat the delivery token as an idempotency key. Transport itself remains
at-least-once; this protocol provides idempotent processing and does not claim a
transactional exactly-once guarantee across Codex tasks.

The normal deployment has one Coordinator writer. A second writer is not
authorized. Multiple route leases represent independent recipient waits, not
multiple state writers. Scheduler revision checks reject stale in-process
plans; multiple writer support would require an external compare-and-swap
journal.

## Priority and isolation

Ready actions are independent from the user-card inbox. Current priority is:

1. direction updates and project questions queued in the Coordinator;
2. queued review or action responses;
3. exact Supervisor or Worker result routes;
4. ready Mission transitions;
5. one individual user card;
6. one changed BLOCKED recovery inspection per lane frontier;
7. one authority reconciliation per changed repository fingerprint;
8. one successor request per completed lane frontier.

Only one short-lived scheduler action is claimed at a time. After its exact
external receipt is recorded, it becomes a route lease and the plan is
recomputed while that result remains outstanding. The initial capacity is
three external routes, with at most one execution route per repository. Each
scheduling pass permits at most one new work start per repository, then rotates
round-robin. Consuming an inbound result and delivering its mandatory next
protocol hop is one handoff chain, not a second work start; it drains to the
next exact external wait or terminal before checkpoint. Equal-priority admission uses the scheduler's durable
`round_robin_cursor_repository_id`; an early v2 state without that field derives
the cursor from its existing lease order without changing any delivery identity.
The new-work-start pass budget remains a primary-Coordinator execution rule;
the scheduler does not claim a durable pass ledger. A completed action is
skipped and an active route's repository is excluded from conflicting execution
sends. An unresponded user card parks only its Mission and cannot suppress
unrelated ready actions.

The plan must expose an active route and a different repository's ready action
at the same time. Counting the active action again as ready, or hiding a ready
successor behind a waiting route, is invalid.

## Successor intent

`allow_request_next_mission=true` is permission, not a perpetual action.

A generic successor request is eligible only when the latest frontier for the
selected repository and supervision lane is `COMPLETE`, or when the repository
has no Mission yet. `USER_DECISION`, `USER_ACTION`, `BLOCKED`, and
`SAFETY_CEILING` do not authorize a generic successor.

The action ID binds repository, lane, exact completed frontier, authority
fingerprint, and exact Supervisor route. A `no_work` or deferred result is
completed against that action ID and is not requested again until a semantic
frontier or authority change produces a new identity. Completed requests are
skipped so an equal-priority repository cannot be starved by stable order.

## BLOCKED recovery

New `blocked` verdicts must persist a v2 `blocked_contract` containing the
origin event/time/evidence, requirement and rationale, positive and negative
qualification examples, completed diagnostics, registered recovery
observation, retry policy, owner, accepted input route, and next permitted
probe. Arbitrary shell commands and phrases such as `environment change` are
not recovery contracts.

The scheduler emits one `inspect_blocked_recovery` action per changed
repository-by-lane blocker revision. Completing it as `unchanged` suppresses
all identical retries. A changed authority or recovery observation creates one
new action. Recovery requests a new Supervisor-authorized recovery Mission;
it does not mutate the historical BLOCKED Mission.

Historical BLOCKED Missions without a complete recovery contract produce one
local `repair_blocker_contract` action. The Coordinator may reconstruct only
their persisted Worker Report and Supervisor verdict evidence; it must not
probe, retry, or infer new facts during migration.

A later exact Supervisor evidence verdict may revise an already complete
contract. Revision is compare-and-swap: the payload names the current contract
fingerprint, has a later origin time and distinct Supervisor evidence, and the
exact event kind/id, repository, Mission, attempt, Supervisor task, and evidence
SHA-256 are bound in `revision_authority`. Under a Mission-scoped file lock, the
old contract is appended to a bidirectionally linked
`blocked_contract_history`. Historical replay is a file-level no-op and only
one of two competing successors can commit. Stale, forged, orphaned, or
conflicting revisions fail closed. The next deterministic plan therefore
consumes the revised Mission contract, not a separately hand-maintained status
summary.

## Ongoing user input

The Coordinator accepts four input classes:

- review or action response: existing exact terminal-response route;
- direction or vision update: `queue-coordinator-event --kind direction_update`;
- project question: `queue-coordinator-event --kind project_question`;
- explicit automation or project stop: apply directly to the named scope.

Direction updates and questions require a repository identity. They route to
that repository's exact Supervisor and never directly to a Worker. The event
is removed from the pending queue only after exact-recipient delivery is
acknowledged. Identical retries deduplicate while an event is pending, but the
same words in a later user turn become a new numbered occurrence after the
prior event was routed. Unrelated project state is unchanged.

## Recovery automation lease

The recovery automation is normally paused.

For actions that send to a Supervisor or Worker:

1. claim one exact action;
2. prepare and persist the delivery envelope;
3. send that envelope and payload to the exact recipient, then persist its
   receipt/cursor;
4. move it to a repository-scoped route lease, release the scheduler claim,
   and repeat from step 1 for another repository while capacity remains;
5. issue one multi-target wait, for at most 60 seconds, containing every exact
   route target and current cursor;
6. consume and persist the first semantic result and complete only its lease;
7. recompute the plan, drain any required protocol handoff to its next exact
   wait or terminal, and fill capacity again;
8. if the wait is unchanged, save the wait set, checkpoint silently, and arm
   recovery only after the foreground turn has stopped;
9. pause recovery when no route lease remains.

An active execution lease suppresses a duplicate ready projection for the same
repository/Mission/attempt and defers same-repository authority maintenance.
A route with a missing cursor is not checkpoint-safe: take one immediate exact
snapshot and attach its cursor to the existing token/hash lease first.

Local-only actions never arm recovery. An unprepared scheduler claim does not
justify a periodic wake; recovery exists for durable prepared deliveries and
route leases only.

If the foreground wait returns a result, no recovery model turn runs. If the
foreground pass checkpoints with unchanged active routes, the recovery wake
resumes only their exact wait set. A `prepared` delivery is reconciled by exact
delivery token before any resend. A wake with no prepared delivery or route
lease must pause itself immediately and must not inspect every project.

## Acceptance boundary

Unit acceptance requires status/plan identity, action deduplication, v1 waiting
claim migration without resend, independent route leases, waiting-A/ready-B
selection, concurrency and per-repository limits, immutable send receipts and
cursors, exact wait-target uniqueness, card isolation, successor fairness,
changed-only BLOCKED recovery, authority content change detection, and
timestamp stability.

It also requires deterministic graph rendering, complete BLOCKED contracts,
duplicate same-Mission route suppression, cursor-complete checkpoints, and a
status-request canary in which an already-arrived Worker Report is delivered to
the exact Supervisor before the status answer returns. A stale portfolio
revision or incomplete active-route set must fail before rendering.

Live acceptance is separate. It requires a two-project canary in which A is
deliberately delayed, B is dispatched and completes while A remains leased,
the multi-target wait consumes one result without damaging the other route,
and an interrupted-route recovery preserves both delivery identities. Green
deterministic tests do not by themselves prove live delivery or human
acceptance.
