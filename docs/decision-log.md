# Decision log

## D-001 — No global completion barrier

- Date: 2026-07-31
- Decision: derive global status from independently runnable Missions; never
  wait for an entire launch set before surfacing a user card.
- Effect: one parked project cannot stop another repository's safe work.

## D-002 — Separate review gate from review depth

- Date: 2026-07-31
- Decision: use `gate=none|required` and
  `depth=light|standard|deep` as independent axes at a named stage.
- Effect: most Supervisor-approved loops continue, while the few human gates
  receive proportionate cards without global scheduling side effects.

## D-003 — Persist the user-response route

- Date: 2026-07-31
- Decision: queue a response before delivery, prioritize it, acknowledge the
  exact Supervisor send, then wait in a distinct Supervisor-adjudication state.
- Effect: terminal packet, Mission identity, pending response, and exact
  Supervisor binding form one resumable causal chain.

## D-004 — Preserve historical Mission evidence

- Date: 2026-07-31
- Decision: do not rewrite old terminal Mission files; suppress cards whose
  exact identities already appear in routed-response history.
- Effect: review inbox accuracy improves without mutating audit evidence.

## D-005 — A consumed continuation is historical, not runnable

- Date: 2026-07-31
- Decision: when the exact successor named by `next_work_order` exists, exclude
  the prior `CONTINUE` from lane resolution, execution selection, and running
  counts.
- Effect: an old continuation cannot outrank the current Mission.

## D-006 — Coordinator lifetime is not a Codex turn

- Date: 2026-08-01
- Status: partially superseded by D-008; logical availability remains, periodic
  idle re-arm does not.
- Decision: run bounded scheduling cycles and automatically re-arm the same
  Coordinator task with a heartbeat. Mission terminals and an all-terminal
  current snapshot never terminate the Coordinator.
- Effect: reviews, comments, Worker results, and new Missions can be handled
  ongoingly without waiting for another manual start message.

## D-007 — In-flight routes keep the current turn alive

- Date: 2026-08-01
- Status: superseded by D-013. Exact route persistence remains; single-route
  foreground monopolization does not.
- Supersedes: the D-006 assumption that every scheduling transition should
  immediately yield to the next heartbeat.
- Decision: after dispatching to an exact Supervisor or Worker, wait on that
  exact task in bounded intervals and consume the result in the same
  Coordinator turn. Checkpoint only when no work is in flight, user/external
  input is required, or an execution/safety ceiling is reached. The heartbeat
  is a recovery watchdog rather than the normal continuation engine.
- Effect: a next-Mission request cannot strand the Coordinator between the
  Supervisor answer and Worker dispatch. Blocked or parked projects still do
  not stop unrelated active routes.

## D-008 — Pause recovery automation while idle

- Date: 2026-08-01
- Supersedes: D-006's always-on heartbeat assumption.
- Decision: user input and same-turn exact-task waits are the normal event
  sources. Arm the recovery automation only while a durable action claim needs
  interruption recovery, then pause it when the route drains.
- Effect: unchanged project state causes zero scheduled model turns. Logical
  Coordinator availability remains independent from automation status.

## D-009 — One plan and durable semantic action identities

- Date: 2026-08-01
- Decision: status, generic resolution, and recovery consume the same
  deterministic plan. Claim one semantic action before acting, persist exact
  send identity, and retain completed action IDs.
- Effect: dispatch-ready work is distinct from in-flight work; unchanged
  cards, blockers, and successor requests do not repeat; stable ordering cannot
  starve later repositories.

## D-010 — Ongoing input is an exact repository event

- Date: 2026-08-01
- Decision: direction/vision updates and project questions enter a durable
  Coordinator event inbox and route to the named repository's exact
  Supervisor. Review replies retain their existing exact Mission route.
- Effect: the user can comment throughout execution without creating project
  tasks or stopping unrelated projects, while Workers remain insulated from
  direct user adjudication.

## D-011 — Prepare an idempotent delivery envelope before transport

- Date: 2026-08-01
- Decision: external actions persist recipient, action ID, payload SHA-256, and
  delivery token before sending. Recovery reconciles a prepared token at the
  exact recipient before an identical resend, and sent/prepared claims cannot
  be released into a fresh retry.
- Effect: the send/receipt interruption window has durable recovery identity.
  Transport remains at-least-once and recipient processing is idempotent; the
  system does not overclaim transactional exactly-once delivery.

## D-012 — Primary Coordinator owns one linked transition map

- Date: 2026-08-01
- Decision: the primary Coordinator emits one user-facing map for a new
  terminal, changed blocker, active user card, legitimate idle checkpoint, or
  forced execution/safety interruption. It separates current work, true
  blockers, active user actions, and the exact resume anchor, and links the
  canonical artifact plus Worker and Supervisor reports. The recovery lease
  stays silent for unchanged state.
- Effect: zero-change heartbeat token use remains eliminated while completed
  work, proposals, reply obligations, and restart identity are discoverable in
  the one Coordinator task. `READY_UNCLAIMED` can no longer be mistaken for a
  blocker or completion.

## D-013 — Separate the scheduler claim from project route leases

- Date: 2026-08-01
- Supersedes: D-007's requirement to keep one exact route in the foreground
  until its result arrives.
- Decision: keep one short-lived scheduler claim for preparation, then move an
  externally sent action to a repository-scoped route lease after its exact
  receipt is durable. Fill up to three independent repository routes, wait once
  on the complete target set for at most 60 seconds, and checkpoint unchanged
  waits with a recovery lease. Process at most one semantic transition per
  repository per pass and rotate round-robin.
- Effect: a slow Worker or Supervisor no longer blocks unrelated READY work,
  repeated foreground waits stop consuming the large Coordinator context, and
  each delivery retains its own token, payload hash, recipient, and cursor.

## D-014 — Make portfolio status and user input lineage durable

- Date: 2026-08-01
- Decision: derive one JSON and one human-readable portfolio index from the
  deterministic plan on semantic changes only. Include every registered
  repository and track Coordinator input from receipt through Supervisor
  disposition and any resulting Mission.
- Effect: current-Mission completion cannot be confused with project
  completion, and the user can locate work, blockers, proposals, decisions, and
  exact next moves without searching hidden runtime folders.

## D-015 — Drain protocol handoffs before a status checkpoint

- Date: 2026-08-01
- Supersedes: D-013's phrase "one semantic transition per repository per pass".
- Decision: limit only new project-work starts. Consuming an arrived result and
  sending its mandatory next protocol hop is one handoff chain that drains to
  the next external wait or terminal. A normal status request performs that
  drain before answering. Active exact routes suppress duplicate ready
  projections, and a route without a cursor is not checkpoint-safe.
- Effect: an observed Worker Report can no longer be reported as "next: send
  to Supervisor" and then left for the next 15-minute recovery wake.

## D-016 — Render one graphical path and evidence-bound stop contract

- Date: 2026-08-01
- Decision: canonical status JSON schema v2 is rendered into one seven-stage
  Mermaid portfolio view. Every blocked or parked row identifies the event,
  time, evidence, reason, positive and negative qualification examples,
  completed diagnostics, owner, input route, and next permitted probe. Legacy
  omissions create one evidence-only contract-repair action.
- Effect: project progress and stopping reasons use one visual grammar, while
  phrases such as "qualifying material" or "environment change" cannot hide
  when, why, or how a requirement arose.

## D-017 — Revise blocker contracts through the Mission ledger

- Date: 2026-08-01
- Decision: a later exact Supervisor evidence verdict may revise a complete
  blocker contract only by naming the current contract fingerprint, carrying a
  later origin time, citing distinct Supervisor evidence, and binding the exact
  event kind/id, repository, Mission, attempt, Supervisor task, and evidence
  SHA-256. Preserve a bidirectionally linked prior contract under a
  Mission-scoped file lock before making the new contract current. Historical
  replay is a no-op and competing successors from one predecessor cannot both
  commit.
- Effect: stale or conflicting updates fail closed, idempotent replay is safe,
  and the scheduler, graphical status, and recovery gate always read the same
  current blocker rather than divergent hand-maintained summaries.

## D-018 — Observe routes with their actual transport

- Date: 2026-08-01
- Corrects: D-013's single undifferentiated multi-target wait.
- Decision: persist the recipient observer on every route. Poll exact ChatGPT
  Supervisor chats once per pass with `read_thread`; include only persistent
  Codex Worker tasks in `wait_threads`. Preserve both classes in the active
  route set and recovery lease.
- Effect: a completed Supervisor result cannot remain hidden because its chat
  ID was sent to an unsupported Codex-task wait API, while Worker waits retain
  bounded first-event behavior.

## D-019 — Adopted runtime repair becomes a typed action

- Date: 2026-08-01
- Decision: an exact Supervisor `ADOPTED` disposition for runtime maintenance
  must create a durable, claimable, evidence-bound recovery action. Execute
  only a fixed allowlisted handler, persist a one-shot effect ledger before
  target mutation, require a receipt, route restricted probes to the exact
  Worker, roll back only under the authorized rule, and return the result to
  the exact Supervisor.
- Effect: “authorization satisfied” can no longer coexist indefinitely with
  zero scheduler actions. Arbitrary shell repair remains forbidden, crash
  recovery is idempotent, and unrelated project routes remain independent.

## D-020 — Transfer scheduler code, recreate scheduler bindings

- Date: 2026-08-02
- Decision: Git transfers the static Coordinator implementation, schemas,
  tests, recovery prompt, and automation portability manifest. Installed live
  `state/`, automation records, primary task IDs, host paths, one-time gates,
  helper caches, and checkpoints remain host-local. A new host creates or
  selects its own exact primary Coordinator and attaches a new recovery
  heartbeat in `PAUSED`; it never copies an old target task ID. The heartbeat
  may become active only when that host's deterministic plan reports
  `watchdog_should_be_armed=true`.
- Effect: a clone can reproduce the development and scheduling structure
  without creating two live writers or silently pointing recovery at a task
  on another machine. Git reflection remains distinct from in-flight live-state
  migration.

## D-021 — Require a monotonic artifact frontier before semantic promotion

- Date: 2026-08-02
- Corrects: delivery acknowledgement as external-action completion, timestamp-
  ordered Mission inference, and schema-v2 portfolio rows without an
  artifact-bound current-frontier certificate.
- Decision: maintain one epoch-CAS FrontierRecord per repository/lane; enforce
  `human > supervisor > worker > repo_observation > coordinator`; retain
  rejected/superseded/parked artifacts as tombstones; bind external results to
  exact thread/turn/message/result and branch/HEAD authority high water; update
  frontier, Mission, scheduler action, and portfolio v3 only through the
  replayable result transaction. Legacy lineage remains `legacy_unverified`.
- Effect: stale evidence can be transported or quarantined but cannot become
  the next ordinary Mission, user review card, generic successor, or current
  portfolio artifact. Missing certificates activate
  `TRANSPORT_ONLY_RECONCILIATION` rather than guessing current state.

## D-022 — Bind every ordinary route to the whole-project current context

- Date: 2026-08-02
- Corrects: Supervisor tunnel vision, lane-local success being mistaken for
  project-wide current position, and stale roadmap/evidence summaries surviving
  after another lane advances.
- Decision: maintain one append-only `ProjectContextRecord` per repository;
  require exact current authority and every active lane frontier; bind the full
  project context into every ordinary Supervisor/Worker action; compare-and-swap
  both project-context revision and artifact-frontier epoch on result; project
  the same record into portfolio schema v4. New and legacy registrations start
  `legacy_unverified` without inferred context.
- Effect: project names and media/game/web domain rules remain outside the
  runtime. An old or narrowed view can be audited or reconciled but cannot
  dispatch work, adjudicate current state, or appear as the current portfolio
  roadmap.

## D-023 — Preserve control-plane waits during context reconciliation

- Date: 2026-08-02
- Corrects: portfolio v4 migration forcing every unverified project row to
  `READY`, which made a valid `WAITING_USER` plan and complete user card
  unrenderable.
- Decision: missing or stale project context invalidates only ordinary
  `RUNNING`/`READY` work. Preserve exact user waits, external route waits,
  blocker/policy parks, and terminal states while replacing the uncertified
  roadmap with the reconciliation projection. Continue to require a complete
  top-level card for every presented `WAITING_USER` row.
- Effect: the renderer can publish the deterministic plan without allowing
  ordinary work from an uncertified context or hiding an actionable user card.
