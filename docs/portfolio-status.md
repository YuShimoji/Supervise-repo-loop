# Durable Coordinator portfolio index

## Decision

The Coordinator maintains two projections of the same deterministic plan:

- `state/coordinator-current-status.v1.json` for schema-v2 machine readback;
- `state/coordinator-current-status.md` for the user.

The filename remains stable so live links do not break. The JSON object's
`schema_version` is authoritative. Markdown is generated from that JSON by
`portfolio-render`; it is never an independently edited status source.

They are runtime artifacts and are never committed to the source repository.
They update atomically only when their semantic fingerprint changes. Timestamps,
file mtimes, active flags, wait counts, and unchanged observations are excluded
from that fingerprint.

```text
python scripts/supervise_repo_loop.py portfolio-render
```

The command validates the v2 stop/progress fields and atomically rewrites only
the Markdown projection.

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
- a seven-stage progress position: Mission, Work Order, Worker, Worker Report,
  Supervisor, Verdict, and Next Route.

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

## User input and proposal lineage

Every Coordinator input receives a durable receipt before routing. Its entry
contains source turn/message identity, target repository and Mission when
known, exact Supervisor recipient, current state, owner/rationale, decision
evidence, and resulting Mission or report.

Allowed states are:

```text
RECEIVED
ROUTED
ADOPTED
DEFERRED
REJECTED
NEEDS_CLARIFICATION
SUPERSEDED
```

Receipt is not adoption. A question may end with an answer report instead of a
Mission. A direction change may be superseded by a later input without erasing
the earlier audit record.

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
are operationally actionable, input disposition and resulting work are linked,
the graph is deterministically rendered from JSON, an observed Worker Report
cannot remain unsent at a normal status checkpoint, and identical semantic
state causes neither a rewrite nor a notification.
