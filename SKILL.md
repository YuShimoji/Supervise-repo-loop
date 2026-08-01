---
name: supervise-repo-loop
description: Coordinate a durable supervision loop through one global user-operated Codex Coordinator, an exact repository-by-lane Web Supervisor, and one persistent repository-by-host Codex Worker. Resolve the repository from Codex context or a stable registered queue, auto-discover or capability-gated create the Worker, preserve Supervisor adjudication, and continue until COMPLETE, USER_DECISION, USER_ACTION, BLOCKED, or SAFETY_CEILING. Use for the fixed triggers "Use $supervise-repo-loop for this repository until terminal state." and "Use $supervise-repo-loop for the next actionable registered repository until terminal state."
---

# Supervise Repository Loop v2

Use the global Coordinator as the only user-operated Codex entry point. Read
[user-operation.md](references/user-operation.md) for the user contract and
[protocol-v2.md](references/protocol-v2.md) before dispatching or reviewing a
Mission. Read [review-isolation.md](docs/review-isolation.md) before operating
more than one repository or emitting a user review card. Read
[scheduler-v3.md](docs/scheduler-v3.md) before selecting, claiming, sending, or
recovering any Coordinator action. Read
[coordinator-task-prompt.md](references/coordinator-task-prompt.md) when
starting, resuming, or changing the one user-facing Coordinator task. Read
[portfolio-status.md](docs/portfolio-status.md) before writing or presenting
the durable all-project status or user-input lineage. Read
[frontier-reconciliation.md](docs/frontier-reconciliation.md) before selecting
ordinary work, applying an external result, presenting review, or promoting a
portfolio row. Read
[project-context-frontier.md](docs/project-context-frontier.md) before any
ordinary Supervisor or Worker send and before applying its result. Use
[recovery-lease-prompt.md](references/recovery-lease-prompt.md) as the exact
attached heartbeat contract. Read
[authorized-runtime-recovery.md](docs/authorized-runtime-recovery.md) before
turning an adopted runtime-repair disposition into an effectful action.

Use `scripts/supervise_repo_loop.py --help` for deterministic resolution,
queueing, binding, migration, state, idempotency, response routing, and
terminal packets. If the normal Python launcher is unavailable, load the Codex
workspace dependencies and use the bundled Python. Do not install a runtime or
package.

## Primary writer fence

Exactly one active Coordinator task may write live orchestration state. Its
identity is `coordinator_state.coordinator_task.task_id`; every mutating CLI
invocation must run in that same task, require the process `CODEX_THREAD_ID` to
match it, and use the canonical paths under the installed skill's `state/` for
the Coordinator record, scheduler, frontier ledger/journal, Missions/events,
project-context ledger, and portfolio projections.
A caller-selected state clone is a test fixture, never an alternate live state
authority.

Repair, audit, reporting, Prompt-design, and development tasks whose task ID is
not the active binding are read-only for live state. They may inspect or test
static copies, but must not claim, prepare, send, complete, release, register or
execute runtime recovery, change a Mission/event, or render the canonical live
portfolio. Never copy, pass, or rewrite the primary ID to impersonate it.

This is a cooperative operational fence, not a malicious-security boundary:
`CODEX_THREAD_ID` and local files are visible to processes running as the same
OS user. Strong isolation against a deliberately hostile full-access process
would require an external credential or service boundary. Rebinding the
primary is a distinct, explicit administrator operation allowed only at a
verified idle edge with no scheduler claim, prepared delivery, route lease, or
recovery-owned runtime phase. Record the old/new identities and authority; a
repair or recovery wake must never rebind implicitly.

## Fixed user triggers

Use these strings unchanged:

```text
Use $supervise-repo-loop for this repository until terminal state.
```

```text
Use $supervise-repo-loop for the next actionable registered repository until terminal state.
```

Do not add a repository name, filesystem location, thread ID, task ID, host
name, or replacement token. Legacy alias resolution remains an internal,
deprecated compatibility path and is not a user entry point.

`until terminal state` scopes only the selected Mission, never the lifetime of
the global Coordinator and never all registered repositories as a batch. An
external send becomes a repository-scoped route lease after its exact delivery
receipt is persisted; it must not retain the global scheduler claim. Fill safe
portfolio capacity from other repositories, then wait once on the complete
exact route set. A foreground wait pass is bounded to 60 seconds. An unchanged
timeout is a quiet durable checkpoint with recovery armed only for the saved
routes, not a reason to narrate or poll one task repeatedly.
Do not keep one turn open merely to wait for one project or every project to
become terminal, and do not treat a cycle final response as Coordinator
completion.

The primary Coordinator owns user-facing transition reporting and the durable
portfolio index named by [coordinator-task-prompt.md](references/coordinator-task-prompt.md).
Update it only for a semantic transition, generate its Markdown with
`portfolio-render`, and embed the same compact seven-stage Mermaid graph in
every state-changing or explicit status response before linking the index. A
publishable status also contains top-level `next_user_action` as one complete
card or `null`, plus each repository's named overall/current/completed/next
roadmap blocks, next gate, and completion definition. Never invent a progress
percentage or leave a `WAITING_USER` row without its entrypoint, requirements,
reply format, post-reply behavior, and non-escalation boundary. A
route-set change is semantic. Before checkpointing, require the portfolio's
scheduler revision, concurrency, active-route count, and exact structured route
set to match the scheduler state used for the response; a stale projection is a
checkpoint blocker, not a presentable status. Also require portfolio schema v4,
the exact frontier revision/safety mode, and a current FrontierCertificate for
every row promoted beyond reconciliation. Also require the exact project-context
revision/safety mode and one current `SupervisorContextEnvelope` on every
ordinary Supervisor/Worker action. A
status request consumes already-arrived route results unless the user explicitly
requests a non-continuing read-only snapshot. `READY` with free capacity must be claimed. If a real turn or safety
ceiling leaves it unclaimed, persist its owner, action ID, deadline, and wake
event. Unchanged recovery wakes remain silent.

## Non-negotiable topology

```text
User
→ one global Coordinator
→ exact Web Supervisor
→ persistent repository × host Worker
→ exact Web Supervisor adjudication
→ the same global Coordinator
→ User only at terminal state
```

- **Coordinator Codex task**: resolve, bind, communicate, persist, and route.
  Do not perform product edits, builds, or tests.
- **Web Supervisor chat**: issue Work Orders and adjudicate Worker Reports.
- **Persistent Codex Worker task**: perform repository work and return a
  self-contained Worker Report. Never self-adjudicate.

Keep Coordinator and Worker separate. Set `in_place=false` in Coordinator
mode. Never create a Worker per Mission, send a Worker Report directly to the
user, or dispatch another Work Order before the exact Supervisor verdict.

`single-thread` is an explicit legacy compatibility mode only. Never infer it
from the current working directory, use it for either fixed trigger, or select
it as repair. Use `binding-repair` only for identity and endpoint records.

## Runtime adapters

Discover these Codex app capabilities on every launch:

- `list_projects`
- `list_threads`
- `read_thread`
- `send_message_to_thread`
- `wait_threads`
- capability-gated `create_thread` for a persistent Codex Worker only

Ordinary Codex task creation is not Web Supervisor chat creation. A Supervisor
may be created only with a verified capability for a regular ChatGPT project
chat.

At launch:

1. List projects and Codex tasks.
2. Resolve the repository from context or the stable queue.
3. Resolve the current host.
4. Validate the exact repository × lane Supervisor binding.
5. Validate, auto-discover, or create once the repository × host Worker.
6. Read back each exact endpoint before activating its binding.
7. Persist only minimal observations in `state/adapter-snapshot.v1.json`.
8. Run the applicable generic dry-run:

   ```text
   python scripts/supervise_repo_loop.py resolve --target this-repository --dry-run
   ```

   ```text
   python scripts/supervise_repo_loop.py resolve --target next-actionable-registered-repository --dry-run
   ```

Never route by a similar title, a recently used repository, or a folder name.
Do not open Browser, Chrome, Electron, media players, or audio playback, and do
not change system volume.

## This-repository resolution

Resolve in this order:

1. Git root bound to the invocation context.
2. Repository root bound to the current Codex project or workspace.
3. Normalized remote identity stored on the current task.
4. Active repository selector.
5. Exactly one pending repository in Coordinator state.

After selection, verify:

- `git rev-parse --show-toplevel`;
- the normalized live Git remote;
- the repository registry record;
- the current host;
- the repository × lane Supervisor binding;
- the repository × host Worker binding.

Normalize SSH and HTTPS spellings to one remote identity. Treat root hints as
host-local and non-authoritative. If context is ambiguous, stop once with the
minimal repository candidates in `USER_DECISION`; do not ask the user for a
filesystem location.

For multiple lanes, use the active Mission lane. Otherwise use the registered
default lane. Never fall back to a similarly named lane.

## Next-actionable resolution

Select only a registered repository with:

- valid remote identity;
- a valid or repairable exact Supervisor binding;
- a valid, discoverable, or creatable Worker binding;
- a ready Mission transition, a changed recovery/authority signal, or one
  unconsumed successor intent from a completed lane frontier;
- authorized work on the current host;
- no duplicate active attempt;
- remaining safety ceiling.

Use this stable priority:

1. a Mission resumable from a user response;
2. Supervisor verdict waiting;
3. Worker result return waiting;
4. Work Order dispatch waiting;
5. pending critical Mission;
6. pending ordinary Mission;
7. registry stable order.

Run `coordinator-plan`, claim its exact action ID, and show the selected action
and reason in Coordinator readback. A short-lived scheduler claim and persisted
repository route leases are separate identities. Never request that the user
re-enter a repository name. `coordinator-status`, generic resolution, recovery,
and the portfolio index must not compute independent answers.

There is no launch-set completion barrier. An unresponded `USER_DECISION` or
`USER_ACTION` parks only its exact Mission and is not an execution candidate.
Continue selecting safe work from other Missions and repositories. Recompute
the project-independent snapshot after every terminal event and response
transition; show only `next_user_card` and keep the remaining cards queued.

Coordinator availability remains `AVAILABLE`; execution separately reports
`READY`, `DRAINING`, `WAITING_USER`, `WAITING_EXTERNAL`, `IDLE`, or
`SAFETY_CEILING`. `READY` means an action has not started; a scheduler claim or
route lease is `DRAINING`. Only a persisted exact send receipt is in flight.
In-flight work and ready capacity may coexist. `all_current_missions_terminal`
never means Coordinator completion.

## Mission admission and quick-win priority

Drain mandatory receipts and exact protocol handoffs before selecting
discretionary new work. A new or continued Mission is admissible only when its
value contract cites the current repository authority revision/fingerprint and
`current_next_action`, then names a concrete `gate_delta` showing how the
smallest deliverable moves that exact gate. Default to one Worker turn and at
most two; a larger slice must explain why a smaller one cannot create usable
value.

Reuse or finish the current artifact before creating another source, story,
form, benchmark, or candidate. If reuse is impossible, name the existing
consumer, the missing property, and why a new artifact is the smallest route to
the gate. A technically interesting result, generic capability proof, or
different topic used only to demonstrate generalization is not sufficient.

Any new artifact, source, story, form, or candidate—and every genre/domain
shift, parallel product direction, exploratory Mission, or strategic bet—
requires explicit user authorization bound in the value contract. Without it,
request a smaller aligned Work Order, return `NO_WORK`, or park the proposal;
do not dispatch it as a quick win. Completion of a legacy Mission does not
authorize an uncontracted successor. An uncontracted legacy Mission that would
otherwise advance or dispatch must project `resolve_mission_value_gate` to its
exact Supervisor. Admit a corrected contract once through the replay-safe
`value_contract_admitted` event, or record `NO_WORK`/park; never overwrite an
admitted contract.

## Ongoing Coordinator cycles

Operate the same global Coordinator as a portfolio event drain with a normally
paused recovery lease:

1. collect allowlisted state and independently observed Git/authority
   high-water signals, load the frontier ledger, then run `coordinator-plan`;
2. handle a newly arrived review response, direction update, or project
   question first;
3. claim only the returned action ID and start at most one new unit of work for
   that repository in the current pass;
4. for an external send, prepare the durable delivery envelope, send its token
   and payload to the exact recipient, persist the receipt/cursor, then move the
   action to a repository-scoped route lease and release the scheduler claim;
5. continue claiming other repositories while capacity remains, up to three
   external routes and one execution route per repository;
6. poll each exact ChatGPT Supervisor route once, then wait once for at most 60
   seconds on only the exact Codex Worker targets and cursors; never pass a
   ChatGPT chat ID to `wait_threads`;
7. consume the first semantic result with
   `coordinator-action-apply-result`; delivery ACK alone is never completion.
   The idempotent reducer validates exact identity and authority observation,
   applies project-context/frontier CAS, Mission, scheduler, and portfolio v4 together, closes
   only that route, and then recomputes the plan; drain the mandatory protocol
   handoff to its next external wait or terminal before checkpoint;
8. on an unchanged timeout, persist the wait set and checkpoint without another
   progress message; arm recovery only after the foreground wait ends;
9. recovery reads only that wait set, and pauses when no route lease remains.

Do not run a periodic idle model wake. Completed action IDs
suppress unchanged cards, blockers, authority revisions, and successor
requests. A `BLOCKED` frontier never authorizes a generic successor; a changed
recovery signal creates one bounded recovery action. A `COMPLETE` lane frontier
permits one Supervisor successor request when repository policy allows it.
An ADOPTED exact runtime-repair disposition creates a typed, evidence-bound
runtime action; it must not remain an unclaimable sentence in the status row.
Only allowlisted handlers may mutate runtime state, and local-effect completion
requires its durable receipt.
`protocol_handoff_required`, `missing_route_cursor`, or a claimable required
handoff forbids checkpoint. Suppress duplicate ready execution projections for
an exact Mission attempt that already owns an execution lease.

## Review policy

New Missions declare independent stage-level axes:

```json
{
  "review_policy": {
    "gate": "none | required",
    "depth": "light | standard | deep",
    "stage": "project-defined stage"
  }
}
```

`gate=none` means exact Supervisor adjudication can continue the loop without a
user review. `gate=required` permits `USER_DECISION` and parks only the exact
Mission. Review depth changes the card contract, never the scheduler scope.
Every review card must bind the exact artifact, entrypoint, criteria, reply
contract, post-reply behavior, and non-escalation boundary.

## Worker auto-binding

For a missing repository × host Worker binding, inspect existing Codex tasks
and accept only candidates whose:

- normalized remote identity matches;
- host identity matches;
- repository root verifies live;
- task is not bound to another repository;
- status is not destroyed, invalid, or stale;
- active Mission does not conflict.

Bind one unique candidate without asking the user. If multiple candidates
remain, emit one minimal `USER_DECISION` in the Coordinator. If none remain,
check both Worker creation capability and `allow_create_worker_task`.

When creation is permitted, create exactly one persistent Worker for the
repository × host, read back its root and remote, activate the binding, and
send `COORDINATOR_WORKER_BOOTSTRAP` from the Coordinator. Never ask the user to
write a Worker bootstrap. If creation is unavailable, emit
`USER_ACTION_CREATE_OR_BIND_WORKER_TASK` in the Coordinator.

Reuse the binding for every later Mission.

## Supervisor routing

Reuse the exact repository × supervision-lane binding. The Coordinator sends:

- `WORK_ORDER_REQUEST`;
- `WORKER_REPORT`;
- `USER_RESPONSE`.

The Coordinator receives:

- `WORK_ORDER`;
- `SUPERVISOR_VERDICT`.

If the exact Supervisor is missing, create it only when the exact ChatGPT
project and regular-chat creation capability are verified and
`allow_create_supervisor_chat=true`. Send and verify the bootstrap handshake
from the Coordinator. Otherwise emit
`USER_ACTION_CREATE_OR_BIND_SUPERVISOR_CHAT` in the Coordinator. Never use
another lane or ask the user to write to the Supervisor.

## Mission routing and adjudication

Persist only this normal causal path:

```text
TRIGGERED
→ REPOSITORY_RESOLVED
→ HOST_RESOLVED
→ BINDINGS_VALIDATED
→ SUPERVISOR_WORK_ORDER_REQUESTED
→ WORK_ORDER_RECEIVED
→ WORKER_DISPATCHED
→ WORKER_RESULT_RECEIVED
→ SUPERVISOR_ADJUDICATION_REQUESTED
→ SUPERVISOR_VERDICT_RECEIVED
→ CONTINUE | COMPLETE | USER_DECISION | USER_ACTION | BLOCKED
```

Only terminal routes add a terminal packet:

- `COMPLETE`
- `USER_DECISION`
- `USER_ACTION`
- `BLOCKED`
- `SAFETY_CEILING`

Use `launch_set_id + mission_id + attempt_id` as dispatch identity. Reject
duplicate dispatch and duplicate Worker Report return. Persist exact
repository, lane, Supervisor, Worker, host, Mission, and attempt identities.
Never re-resolve the lane mid-Mission.

After `WORKER_RESULT_RECEIVED`, request exact Supervisor adjudication before
anything else. A rejected artifact becomes `review_status=rejected` and
`mission_status=superseded` before a successor. `CONTINUE` creates a new
attempt. At the ceiling, preserve the next Work Order and emit
`SAFETY_CEILING`.

## Work Orders and Worker Reports

Compile compact Work Orders with:

```text
python scripts/supervise_repo_loop.py compile-work-order --mission mission.json --output packet.json
```

The packet hash-binds [protocol-v2.md](references/protocol-v2.md) and contains
only Mission-specific identities, scopes, artifacts, authority revisions,
acceptance deltas, STOP deltas, and external-effect state. Send it to the bound
Worker with `[SUPERVISOR WORK ORDER]`.

Require the Worker Report to contain repository, Mission, attempt, Worker,
host, result classification, active artifact, verification, deviations,
bounded blocker, suggested decision type, Git state, external effects, and the
complete report. Send it intact to the exact Supervisor with
`[SUPERVISION CONTROL — WORKER RESULT]`.

Accept only `accept`, `bounded_repair`, `reject`, `continue`, `complete`,
`user_decision`, `user_action`, or `blocked`. Nonterminal continuation requires
a non-empty next Work Order. `blocked` requires a complete v2 recovery contract
with origin event/time/evidence, requirement and rationale, qualifying and
non-qualifying examples, diagnostics completed, owner, input route, baseline
fingerprint, retry policy, and next permitted probe. If an old BLOCKED Mission
lacks this, run one evidence-only `repair_blocker_contract` action; do not retry
the project during reconstruction. If a later exact Supervisor verdict narrows
or replaces a complete blocker contract, record it as a revision: it must name
the current contract fingerprint, be newer, use distinct Supervisor evidence,
bind the exact event kind/id, repository, Mission, attempt, Supervisor task and
evidence SHA-256 in `revision_authority`, and preserve the prior contract in a
bidirectionally linked `blocked_contract_history`. Apply revisions under the
Mission file lock; a historical replay is a no-op and two revisions naming the
same predecessor cannot both succeed. A status-only projection must never
diverge from the Mission's current contract.

## User responses

When a Mission stops at `USER_DECISION` or `USER_ACTION`, accept freeform text
only in the Coordinator. Normalize it to:

- `repository_id`
- `mission_id`
- `attempt_id`
- `terminal_route_being_resumed`
- `raw_user_response`
- `related_artifact`
- `current_external_effect_state`

Send this `USER_RESPONSE` to the exact Supervisor. Never send it directly to
the Worker and never let the Coordinator decide accept, reject, or bounded
repair.

Queue the normalized response durably before attempting delivery. This queue
item is priority 1 and must share the exact repository, Mission, and attempt
identity with the parked Mission. A successful exact-Supervisor send records
`delivery_acknowledged` on the same pending item; it does not remove the item,
advance the Mission, adopt the direction, or complete the route. Only an exact
Supervisor result applied by the frontier transaction moves the input to its
semantic disposition and resumes only that Mission. Supervisor acceptance
after a user reply may complete the Mission without another Work Order; repair
and continuation still require a non-empty next Work Order.

## Terminal handling

Write and deduplicate terminal packets before attempting the silent
notification. Notification failure must preserve the packet and must not
change the Mission to `BLOCKED`. Never notify intermediate progress.

Show the user only the Supervisor-confirmed terminal packet or the
Coordinator-generated `SAFETY_CEILING`. The response destination is always the
same Coordinator.

Emit each user terminal immediately. Do not wait for other repositories or for
all Missions in a launch set to finish. Present one complete card at a time;
the Coordinator snapshot retains the rest. A historical terminal whose exact
identity already has a routed response is handled evidence, not a new card.

Do not commit, push, create a PR, merge, tag, release, publish, deploy, change
access, create a live endpoint, send a live message, or emit a live toast
merely because this orchestration skill ran. External effects require their
own authority and remain separate from Mission status.
