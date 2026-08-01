# Project-independent review scheduling

## Problem

The Coordinator previously had per-Mission terminal states but no durable
projection that separated the execution queue from the user-review queue. In
practice this allowed four different concerns to collapse into one barrier:

1. a launch set was treated as the user-visible unit of completion;
2. `USER_DECISION` was presented after unrelated repositories finished;
3. review necessity and review depth were treated as the same decision; and
4. `route-user-response` prepared a packet but did not persist a resumable
   queue item or a post-send Supervisor-adjudication state.

This made a terminal packet look resumable while its normal causal chain was
absent. It also allowed already-routed historical terminal files to reappear as
stale review cards.

## Operating model

The Coordinator has two independent projections over the same Mission state:

- **execution queue**: work that can progress through Supervisor or Worker;
- **user-card inbox**: one exact `USER_DECISION` or `USER_ACTION` card at a
  time.

There is no launch-set completion barrier. A user gate parks only the exact
Mission that emitted it. Other Missions in the same repository may continue
only when their lane and authority are independent; other repositories remain
eligible by default.

The user-facing project states are:

- `RUNNING`;
- `READY`;
- `WAITING_USER`;
- `WAITING_EXTERNAL`;
- `SYSTEM_BLOCKED`;
- `MISSION_COMPLETE_NEXT_UNSELECTED`;
- `PARKED_BY_POLICY`;
- `PROJECT_COMPLETE`.

Internal Mission projections may retain compatibility labels, but the durable
portfolio index must not use `complete` for both a terminal Mission and an
explicitly finished project.

The Coordinator global state remains `RUNNING` whenever at least one project
can progress. It becomes `AWAITING_USER_ONLY` only when no execution work is
available and one or more user cards remain.

## Ongoing lifecycle

Project independence does not make a Codex turn itself persistent. After an
exact Supervisor or Worker send, the Coordinator persists the receipt and
cursor as a repository route lease and releases the portfolio scheduler to
another repository. It fills safe capacity before waiting once on the complete
exact route set. A foreground wait pass lasts at most 60 seconds; an unchanged
timeout saves the wait set and checkpoints without another progress message.
The recovery heartbeat is paused while idle and while the primary foreground
wait is active. It is armed only after a checkpoint leaves durable prepared
deliveries or route leases. Local-only claims never arm it.

Coordinator availability is `AVAILABLE`; execution separately reports
`DRAINING`, `WAITING_USER`, `WAITING_EXTERNAL`, or `IDLE`. None is a
Coordinator terminal state. `all_current_missions_terminal=true` is only an
observation. `cycle_should_rearm` is true only while recovery is armed for an
active exact claim, never merely because the Coordinator remains available.

Each pass uses one deterministic plan, claims one short-lived scheduler action,
persists its result or converts its send to a route lease, and recomputes.
In-flight routes do not hide different repositories' ready actions. A
checkpoint with routes is allowed only after the bounded wait, when all exact
delivery identities and the next recovery event are durable and no safe ready
capacity remains. Completed action IDs prevent repeated card presentation,
unchanged blocker probes, and repeated successor requests. Idle checkpoints do
not run a model heartbeat.

## Review policy

Every new Mission carries a stage-level policy:

```json
{
  "review_policy": {
    "gate": "none | required",
    "depth": "light | standard | deep",
    "stage": "project-defined stage name"
  }
}
```

The two axes are deliberately independent:

| Gate | Depth | Meaning | Scheduling effect |
|---|---|---|---|
| `none` | any | Supervisor evidence review is sufficient | loop continues |
| `required` | `light` | narrow direction or confirmation check | exact Mission parks |
| `required` | `standard` | artifact review against an explicit contract | exact Mission parks |
| `required` | `deep` | high-consequence or multi-criterion human review | exact Mission parks |

`deep` never means “stop every project.” `gate=required` never grants external
effects. Publication, release, rights, production, deployment, access, and Git
integration remain separate authorities.

Card depth is enforced as follows: `light` has at least one explicit criterion,
`standard` at least two, and `deep` at least three plus an evidence summary and
the risk of a wrong decision.

## Card contract

A `USER_DECISION` card must bind the decision to one exact artifact and contain:

- artifact identity;
- artifact entrypoint;
- explicit criteria;
- reply contract;
- post-reply behavior;
- non-escalation boundary.

Cards are emitted immediately when their Mission reaches a user terminal. The
snapshot exposes only `next_user_card`; the remaining count keeps review
one-at-a-time without losing queued cards. A routed historical response
suppresses its old terminal card.

## Response causal path

Freeform Coordinator replies use a two-phase durable route:

```text
USER_DECISION | USER_ACTION
→ USER_RESPONSE_QUEUED
→ priority 1 individual resume
→ exact Supervisor send succeeds
→ SUPERVISOR_USER_RESPONSE_ADJUDICATION_REQUESTED
→ Supervisor verdict
→ COMPLETE | CONTINUE | USER_DECISION | USER_ACTION | BLOCKED
```

Queueing persists the exact repository, Mission, attempt, terminal route,
Supervisor recipient, raw reply packet, response identity, and priority. Send
acknowledgement removes the pending item only after exact-recipient verification
and records it in `routed_user_responses`.

The Coordinator does not classify `accept`, `bounded_repair`, or `reject`.
After a user response, the exact Supervisor may accept and complete without a
new Work Order. Repair or continuation still requires a non-empty next Work
Order.

## Scheduler priority

1. queued direction update or project question for an exact repository;
2. queued user response for an exact parked Mission;
3. Supervisor verdict, including a user-response verdict;
4. Worker result return;
5. Work Order dispatch;
6. critical or ordinary Mission transition;
7. one individual user card;
8. changed BLOCKED or authority reconciliation;
9. one successor request for an unconsumed completed lane frontier.

An unresponded user card is not an execution candidate. Therefore it cannot
win the queue and cannot prevent another repository from progressing.

Every selected item has a semantic action ID. Claim it before acting and retain
the completed ID. An external receipt moves the action to a repository route
lease so another repository can be selected while it waits. Limit the initial
portfolio to three external routes and one execution route per repository;
rotate repositories after at most one new work start per pass. Result receipt
and its mandatory Supervisor/Worker handoff drain to the next exact wait or
terminal before rotation; this protocol completion is not another work start.

## Compatibility

Historical v2 Missions without `review_policy` are interpreted as
`required / standard / legacy-mission` only when they already contain a
`USER_DECISION`. They are not rewritten. Historical terminal files whose exact
identity appears in `routed_user_responses` are treated as handled and are not
shown again. A historical `CONTINUE` is no longer actionable once the exact
Mission/attempt named by its `next_work_order` exists.

## Acceptance proof

The E2E acceptance route must demonstrate all of the following in one test:

1. a normal Supervisor verdict parks repository A at `USER_DECISION`;
2. its complete card is emitted immediately;
3. repository B remains selected and active;
4. a freeform reply is durably queued and selects A at priority 1;
5. delivery is acknowledged only for A's exact Supervisor;
6. A waits at user-response adjudication priority 2;
7. Supervisor acceptance completes A while B remains unchanged.

Scheduler acceptance additionally proves that status and plan return the same
action IDs, a card and completed successor frontier are consumed once, a
dispatch-ready transition is not labeled in-flight before exact send, A can
remain waiting while B is claimed and completes, each route preserves a unique
token/cursor, unchanged BLOCKED evidence is quiet, and timestamp-only changes
do not create a wake. A normal status request consumes an already-arrived exact
result and sends its mandatory next hop before presenting the generated
seven-stage graph. Every true system blocker or policy park carries an
evidence-bound stop contract; an ordinary external wait instead carries its
exact route identity. The portfolio revision and complete route set must match
the scheduler before checkpoint. Live task delivery remains a separate
two-project canary.
