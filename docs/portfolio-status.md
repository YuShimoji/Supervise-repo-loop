# Durable Coordinator portfolio index

## Decision

The Coordinator maintains two projections of the same deterministic plan:

- `state/coordinator-current-status.v1.json` for schema-v4 machine readback;
- `state/coordinator-current-status.md` for the user.

The filename remains stable so live links do not break. The JSON object's
`schema_version` is authoritative. Markdown is generated from that JSON by
`portfolio-render`; it is never an independently edited status source.

They are runtime artifacts and are never committed to the source repository.
The JSON source projection updates only when its semantic fingerprint changes.
Markdown is then regenerated from the validated JSON and replaced atomically as
its own file. A status checkpoint is publishable only after the renderer confirms
that both projections describe the same semantic fingerprint and that the JSON
records the exact current scheduler revision and active route set. The two files
are not claimed to change in one cross-file atomic transaction. Timestamps, file
mtimes, active flags, wait counts, and unchanged observations are excluded from
that fingerprint.

```text
python scripts/supervise_repo_loop.py portfolio-render
```

The command reads the live scheduler, frontier, and project-context ledgers by default. It
validates the stop/progress fields, scheduler revision, concurrency,
active-route count, exact structured route identities, semantic fingerprint,
frontier revision, project-context revision, and every repository certificate
before upgrading/writing the canonical JSON and atomically rewriting the
Markdown projection. Schemas v2/v3 remain readable only for migration; they are
not publishable as a current project context. A stale JSON source
fails before output is changed.

The top-level `active_routes` array contains the repository, action, exact
recipient, recipient transport/observer, delivery token, cursor, and status for
every prepared or external route. Its order is presentational; its identity set
must exactly equal the scheduler's capacity-reserving route set. Codex Worker
routes use `wait_threads`; ChatGPT Supervisor routes use one bounded
`read_thread` poll per pass and must never be silently omitted from observation.

## Repository rows

The index contains every registered repository, including repositories with no
Mission. Each row contains:

- repository and supervision lane;
- one canonical project state;
- current Mission and attempt, or `none`;
- why it is or is not running;
- scheduler claim or route-lease owner;
- last semantic evidence identity;
- exact next move;
- unblock, reselection, or completion condition;
- whether the user can or must act;
- artifact, Worker Report, and Supervisor verdict links when available;
- frontier status, epoch/event, disposition, source actor, branch/HEAD,
  authority fingerprint, and exact active artifact certificate;
- project-context status/revision/event plus its north star, current bottleneck,
  completion definition, active-lane frontier map, decisions, and evidence
  coverage;
- a seven-stage progress position: Mission, Work Order, Worker, Worker Report,
  Supervisor, Verdict, and Next Route;
- a roadmap position with the overall position, current block, completed
  blocks, next blocks, next gate, and explicit project completion definition.

The roadmap position is copied from the certified `ProjectContextRecord`. It
uses named blocks and gates, not a guessed completion
percentage. It shows where the current slice sits in the known project process
without implying acceptance for unfinished owner gates.

Use only:

```text
RUNNING
READY
WAITING_USER
WAITING_EXTERNAL
SYSTEM_BLOCKED
MISSION_COMPLETE_NEXT_UNSELECTED
PARKED_BY_POLICY
PROJECT_COMPLETE
```

`PROJECT_COMPLETE` requires an explicit project-terminal policy or owner
decision. A terminal Mission alone produces
`MISSION_COMPLETE_NEXT_UNSELECTED`, `PARKED_BY_POLICY`, or a successor-ready
state.

## Block entries

A block entry is a v2 recovery contract. It contains the exact event, time, and
evidence that introduced the requirement; purpose, effect, requirement and
rationale; concrete `qualifies_when` and `does_not_qualify` examples;
diagnostics already completed; state and owner; accepted input destination and
format; baseline observation fingerprint; retry policy; and next permitted
probe. `New material`, `environment change`, and similar shorthand are invalid
without those fields. Historical blockers do not poison a later successful
Mission frontier.

A legacy BLOCKED frontier without that contract is not silently summarized. It
produces one `repair_blocker_contract` action that may only reconstruct facts
from the persisted Worker Report and Supervisor verdict.

When an exact Supervisor disposition adopts a bounded runtime repair, the row
links its typed runtime action and shows its current effect/probe/receipt phase.
The adopted action is no longer described as waiting for an unspecified
environment change. See
[authorized-runtime-recovery.md](authorized-runtime-recovery.md).

A later exact Supervisor verdict can narrow a complete contract. The new
contract must carry `supersedes_contract_fingerprint` for the current Mission
contract, a later origin time, distinct Supervisor evidence, and an exact
`revision_authority` binding with the evidence SHA-256. The prior contract
remains in a bidirectionally linked `blocked_contract_history`; Mission-file
locking permits only one successor for a predecessor and makes historical
replay a no-op. Regenerate the portfolio only after the Mission ledger accepts
that revision, so the table and scheduler cannot describe different blockers.

`WAITING_EXTERNAL` and `WAITING_USER` describe an exact route or review wait;
they are not blocker classifications and do not require a fabricated recovery
contract. Only `SYSTEM_BLOCKED` and `PARKED_BY_POLICY` require `stop`. If a wait
row also carries a real stop contract, it is validated but remains secondary to
the exact route identity. The graph renders normal external waits in blue,
user/review waits and policy parks in amber, and only system blockers in red.

Project-context reconciliation gates ordinary work; it does not erase a
durable control-plane state. When a row lacks a current ProjectContextRecord,
the v4 migration changes `RUNNING` or an ordinary `READY` row to reconciliation
`READY`, but preserves `WAITING_USER`, `WAITING_EXTERNAL`, blocker/policy parks,
and terminal states. A preserved `WAITING_USER` row must still identify the
same complete top-level `next_user_action`. This keeps the renderer aligned
with the deterministic plan while the project-wide roadmap remains explicitly
uncertified.

A repository may simultaneously own a Mission-scoped `WAITING_USER` card and
an independent control-plane route. Its row remains `WAITING_USER` so the card
stays actionable, while `active_routes` and `route_owner` retain the exact
control action, recipient, observer, token, and cursor. Top-level execution is
still draining or waiting externally. Refreshing another route result must not
rewrite that row to `WAITING_EXTERNAL` or drop the card. The complete top-level
card remains authoritative for its Mission row when that repository's own
control route remains active, closes, or is replaced by its mandatory context
route; scheduler projection still independently determines `active_routes`,
`route_owner`, and top-level execution.

## User input and proposal lineage

Top-level `next_user_action` is either `null` or the one complete card the user
can act on now. When present it includes the exact project, decision/action
kind, purpose, why now, entrypoint, every requirement, reply format, owner,
post-reply behavior, and non-escalation boundary. Any `WAITING_USER` repository
must have that card; the user never has to inspect another task or hidden state
file for the missing instructions. Other user-waiting Missions remain parked
until they become the one presented card.

Every Coordinator input receives a durable receipt before routing. Its entry
contains source turn/message identity, target repository and Mission when
known, exact Supervisor recipient, current state, owner/rationale, decision
evidence, and resulting Mission or report.

Allowed states are:

```text
RECEIVED
DELIVERY_ACKNOWLEDGED
ROUTED
ADOPTED
DEFERRED
REJECTED
NEEDS_CLARIFICATION
SUPERSEDED
```

Receipt and delivery acknowledgement are not adoption or routing completion.
The pending input remains pending until an exact Supervisor result is validated
and applied with its frontier event. A question may end with an answer report
instead of a Mission. A direction change may be superseded by a later input
without erasing the earlier audit record.

## Presentation

Every state-changing primary Coordinator response and every explicit status
answer embeds the same compact Mermaid progression plus the project table, then
links the same Markdown index. A link by itself is insufficient. Before a
normal status answer, the primary Coordinator consumes any result already
available on an exact route and drains the required handoff to the next
external wait or terminal. Repeated waits and unchanged recovery wakes produce
no new index version and no user message. The index is a projection, not an
independent scheduler; action, route, and resume identities must come from the
same plan revision used for execution.

## Acceptance

Acceptance requires behavior tests proving all registered repositories are
present, Mission completion is not rendered as project completion, block rows
are operationally actionable, one next-user card is complete, every row exposes
its roadmap position, input disposition and resulting work are linked, the
graph is deterministically rendered from JSON, an observed Worker Report cannot
remain unsent at a normal status checkpoint, and identical semantic state
causes neither a rewrite nor a notification.
