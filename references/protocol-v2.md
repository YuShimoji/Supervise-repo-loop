# Supervision Protocol v2

This file is the hash-bound common protocol shared by every compact Work Order.
Mission packets contain only deltas.

## Roles

- One global user-operated Coordinator resolves repositories, binds endpoints,
  routes packets, and persists state; it does no product work.
- Web Supervisor issues Work Orders and adjudicates Worker Reports.
- Persistent Codex Worker performs repository work and never self-adjudicates.
- User writes only in the Coordinator and receives only Supervisor-confirmed
  terminal decisions and actions there.

## Repository safety

- Read the nearest repository `AGENTS.md` and the authority files it names.
- Preserve unrelated tracked, staged, dirty, ignored, untracked, protected, and
  user-owned state.
- Do not stash, reset, clean, force, rewrite history, or switch branches unless
  the Mission and repository authority explicitly permit it.
- Treat commit, push, PR, merge, tag, release, deployment, publication, upload,
  paid action, credentials, access, rights, and human acceptance as separate
  authorities.
- Bind claims and acceptance to exact revisions and artifact hashes.

## Runtime safety

- Do not launch Browser, Chrome, Electron, media players, speaker previews, or
  audio playback unless the Mission explicitly authorizes it.
- Keep playback silent by default and never change system volume.
- Do not install dependencies unless the Mission explicitly permits it.
- Do not infer private, ignored, untracked, protected-checkout, or
  application-local artifacts on another host.

## Causal routing

- Resolve the repository from verified Codex context or the stable registered
  queue; never from title similarity or recent use.
- Use one persistent Worker per repository × host.
- Use one exact Supervisor per repository × supervision lane.
- Auto-bind a unique verified Worker candidate. Create one persistent Worker
  only when no candidate exists and both capability and policy allow it.
- Never fall back by similar title or switch lanes during repair.
- Return every Worker outcome to the Supervisor before any next Mission,
  successor artifact, or user request.
- A Worker may suggest a decision but cannot emit the user-facing verdict.
- Dispatch and report return are idempotent per Mission attempt.
- Route freeform user responses Coordinator → exact Supervisor. Never send a
  user response directly to the Worker.
- Never use completion of a launch set as a prerequisite for showing a user
  terminal or continuing another repository.

## Project-independent scheduling

- Maintain an execution queue separately from the user-card inbox.
- A user terminal parks only its exact Mission. Other safe Missions remain
  eligible, including independent work in other repositories.
- Present the next complete card immediately and one at a time; retain the
  remaining card count without batching their review into one decision.
- Suppress a historical terminal card when its exact repository, Mission, and
  attempt identity already has a routed user response.
- Use stage-level `review_policy.gate` for whether user review is required and
  `review_policy.depth` for `light`, `standard`, or `deep` review. Depth never
  broadens the parking scope.

## Coordinator lifecycle

- Codex turns are deterministic claim-drains, not the Coordinator lifetime.
- `until terminal state` applies to the selected Mission only.
- Reaching terminal state in every currently known Mission produces an idle
  checkpoint, not Coordinator completion.
- `coordinator-status`, generic next-action resolution, and recovery use the
  same scheduler plan and action ID.
- A normal status/list/why-stopped request first inspects each exact route once
  with its persisted observer: `read_thread` for a ChatGPT Supervisor and
  `wait_threads` only for Codex Workers. An already-arrived result is consumed and its mandatory protocol
  handoff drains to the next external wait or terminal before the answer. A
  status request is not an implicit read-only freeze.
- Claim an action before presentation, mutation, or send. Keep the scheduler
  claim only through preparation and receipt persistence. A send is in flight
  only after its exact recipient, token, payload hash, and cursor are persisted
  as a repository-scoped route lease.
- A route lease is not a global scheduler lock. Fill safe capacity from other
  repositories, up to three external routes and one execution route per
  repository, before waiting.
- Before every external send, persist the delivery envelope returned by
  `coordinator-action-prepare` and include its action ID, delivery token, and
  payload SHA-256 in the exact message. Reconcile a prepared but unconfirmed
  delivery by reading that recipient for the token before an identical resend.
  Transport is at-least-once with idempotent processing; do not claim
  transactional exactly-once delivery.
- Poll each exact ChatGPT Supervisor route once, then wait once for at most 60
  seconds on the Codex Worker route set and consume the first semantic result.
  A ChatGPT chat ID is never a `wait_threads` target. An unchanged timeout is
  not a result event and must not produce another progress message or task
  reread.
- The recovery heartbeat remains paused while idle and while the primary
  foreground wait is active. Arm it only after a foreground checkpoint leaves
  persisted external route leases, and pause it after the route set drains.
- A checkpoint is permitted after the bounded foreground wait when every
  in-flight identity and next wake is durable, even if external routes remain.
  It is not permitted while ready capacity can safely be filled. A completed
  semantic action must not repeat an unchanged user card, blocker, authority
  revision, successor request, or portfolio index.
- Result consumption plus the required next protocol hop is one handoff chain,
  not a second project work start. `protocol_handoff_required`, a missing route
  cursor, or a claimable handoff action forbids checkpoint. An active execution
  lease suppresses another ready execution projection for the same exact
  Mission attempt.

No-action is `IDLE_CHECKPOINT` with no terminal route. It is never Coordinator
`COMPLETE`.

## Ongoing Coordinator input

- Review and action responses resume only their exact terminal Mission.
- Direction or vision updates and project questions are persisted in the
  Coordinator event inbox and receive a durable receipt before routing to the
  named repository's exact Supervisor.
- Questions and direction changes never route directly to a Worker and never
  park unrelated projects.
- If the repository target is ambiguous, ask only for that target before
  queueing the event.
- Persist `RECEIVED`, `ROUTED`, and the Supervisor disposition `ADOPTED`,
  `DEFERRED`, `REJECTED`, `NEEDS_CLARIFICATION`, or `SUPERSEDED`, together with
  decision evidence and any resulting Mission identity. Receipt is not
  acceptance.

## Successor and recovery identity

- `allow_request_next_mission` grants permission but does not create a
  perpetual action.
- Request at most one successor for an exact completed repository-by-lane
  frontier and authority fingerprint. A no-work result consumes that identity.
- `USER_DECISION`, `USER_ACTION`, `BLOCKED`, and `SAFETY_CEILING` do not grant a
  generic successor.
- A BLOCKED verdict must carry a durable v2 recovery contract with origin
  event/time/evidence, requirement, rationale, positive and negative
  qualification examples, completed diagnostics, owner, input route, baseline
  fingerprint, retry policy, and next permitted probe. Retry only after its
  semantic observation changes; recovery creates a new Mission and preserves
  the historical blocker. Legacy omissions create one evidence-only contract
  repair action, not a retry.
- A later exact Supervisor evidence verdict may revise a complete blocker
  contract only by naming the current contract fingerprint. The verdict must
  be newer and evidence-distinct, and `revision_authority` must bind its exact
  event kind/id, repository, Mission, attempt, Supervisor task, and evidence
  SHA-256. Preserve the previous contract in bidirectionally linked Mission
  history before atomically making the revision current under the Mission file
  lock. Historical replay is a no-op; competing revisions from one predecessor
  cannot both succeed. Status and scheduler both read that same current Mission
  contract.
- An `ADOPTED` runtime-repair disposition is converted to a typed scheduler
  action bound to that exact evidence SHA-256, authority ID, Mission identity,
  handler, target identity, Worker, and Supervisor. Only allowlisted handlers
  may execute. A prepared local effect is non-releasable and requires a durable
  receipt; precondition mismatch performs no target mutation and leaves the
  authority unconsumed. See
  [authorized-runtime-recovery.md](../docs/authorized-runtime-recovery.md).

## Progress presentation

- Canonical status JSON uses schema version 2 and is rendered, not separately
  rewritten, into the Markdown index.
- It records the exact scheduler revision and structured active route set used
  to build the response. Revision, concurrency, route count, repository,
  action, recipient, observer kind, token, cursor, or route-status mismatch forbids a
  checkpoint until JSON is regenerated and Markdown is rendered and verified.
- Every state-changing or explicit status response embeds the same compact
  Mermaid path: Mission -> Work Order -> Worker -> Worker Report -> Supervisor
  -> Verdict -> Next Route.
- Every project marks its exact current stage, reason, owner, and next move.
  Waiting rows expose the full recovery contract. A file link alone is not a
  graphical progress answer.

## State separation

Keep these states independent:

```text
mission_status:
  pending | running | complete | blocked | rejected | superseded

review_status:
  not_required | pending | accepted | bounded_repair | rejected

external_effect:
  not_required | pending | complete | blocked | unverified
```

External effects are `transport`, `recipient_open`, `upload`, `publication`,
and `release`. A completed Mission with pending transport and unverified
recipient-open is legal.

## Worker Report contract

Return a self-contained report containing repository, Mission, attempt, Worker,
host, result classification, active artifact, verification, deviations,
bounded blocker, suggested decision type, Git state, every external effect, and
the complete human-readable report. Never claim Supervisor acceptance.

## Terminal contract

Only Supervisor verdicts produce `COMPLETE`, `USER_DECISION`, `USER_ACTION`, or
`BLOCKED`. The Coordinator may additionally stop at `SAFETY_CEILING`.
Notification is silent, best-effort, and never changes Mission classification.

A user response is resumable only through this persisted path:

```text
USER_DECISION | USER_ACTION
→ USER_RESPONSE_QUEUED
→ exact Supervisor send acknowledged
→ SUPERVISOR_USER_RESPONSE_ADJUDICATION_REQUESTED
→ Supervisor verdict
```

The pending queue item and parked Mission must share exact repository,
Mission, and attempt identity. User-response acceptance may complete the
Mission without a new Work Order. Repair or continuation still requires one.
