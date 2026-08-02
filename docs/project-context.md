# Project context

## Thesis

`supervise-repo-loop` lets one user-facing Coordinator safely operate many
repository-specific Supervisor and Worker loops without exposing internal
bindings or allowing one project's human gate to stall unrelated work.

## Current development axis

Keep the portfolio scheduler deterministic while adding a generic project-wide
context frontier above each lane's artifact frontier. No Supervisor or Worker
may act from a stale roadmap, narrowed lane view, or older evidence set.

## Current slice

The static source has been moved into a dedicated development repository while
the installed runtime retains the only live `state/`. The Coordinator Prompt
now defines bounded cross-project route capacity, one multi-target wait, a
durable all-project index, canonical Mission/project states, and user-input
lineage. Scheduler schema v2 and its v1 migration are the active implementation
slice. Existing delivery tokens and live state must remain intact during
deployment.

The active corrective sub-slice closes a live status checkpoint defect: a
completed Worker Report was observed during a status request but remained
unsent until the next recovery wake. Protocol completion now drains separately
from the per-project new-work budget. The same sub-slice adds deterministic
seven-stage graphical status and evidence-bound v2 stop contracts.

A later live audit exposed two additional must-fix gaps. First, an ADOPTED
NLMYTGen runtime-repair disposition had no claimable scheduler representation,
so the project remained blocked after all required authority was present.
Second, ChatGPT Supervisor chats were incorrectly projected into the same
`wait_threads` set as Codex Workers, although that observer supports Codex
tasks only; an already-completed FastFictionFactory disposition therefore
remained leased. The current sub-slice adds typed one-shot runtime effects and
transport-aware `poll_targets`/`wait_targets`.

The 2026-08-02 efficiency correction binds all live mutation to the one exact
primary Coordinator task and canonical installed paths. Repair/audit tasks can
read the plan but cannot claim, deliver, mutate Mission/event ledgers, run
runtime recovery, compile/migrate live artifacts, or render canonical status.
The scheduler now diverts uncontracted legacy Missions to a Supervisor value
gate instead of a Worker. Portfolio status carries one complete next-user card
or explicit none plus each project's named roadmap position.

The cross-host handoff correction makes that single-writer boundary portable.
Git now carries a host-neutral automation manifest and a read-only checkout
verifier. Installed live state, Codex automation records, primary task IDs,
one-time gates, helper caches, and checkpoints remain local to their host; a
receiving host recreates its bindings from the static contracts instead of
copying the sending host's identities.

The 2026-08-02 frontier-integrity correction addresses a separate control-plane
regression: a transport acknowledgement or stale Coordinator summary could
close an external action and re-promote an older artifact. Static source now
has a per-repository/lane monotonic frontier ledger, source precedence,
epoch-CAS external results, branch/HEAD and authority high-water certificates,
portfolio schema v3, and `TRANSPORT_ONLY_RECONCILIATION`. Legacy live state is
not inferred or rewritten by this repair lane; it remains
`legacy_unverified` until the exact primary Coordinator performs a later
authorized reconciliation.

The following project-context correction closes the remaining scope gap. One
append-only `ProjectContextRecord` now binds north star, roadmap position,
bottleneck, completion definition, every active lane frontier, decisions, and
evidence coverage. Every ordinary external action carries a deterministic
`SupervisorContextEnvelope`; results are accepted only if both the project
context revision and lane frontier epoch still match. Portfolio schema v4
projects that same record, so a safe action plan and the user-visible current
position cannot silently diverge. The runtime contains no product-name branch;
four existing migration shapes and new web/game/media registrations use the
same contract.

The current root-loop correction addresses the live revision-194 stop. The
earlier Coordinator recorded frontier reconciliation audits and abstentions as
locally completed actions even though the frontier ledger stayed at revision
zero. Those stable action IDs then suppressed every retry, leaving no active
route; the unrelated FastFictionFactory user card made the whole portfolio
look `WAITING_USER`. Frontier and project-context reconciliation are now exact
Supervisor external routes with typed results. A legacy local completion no
longer shares their identity, and a present user card cannot hide claimable
work in another project.

## Final deliverable image

The Coordinator always shows one actionable user card, continues safe READY
work while unrelated routes wait, accepts natural-language replies, and resumes
only the exact Mission through its bound Supervisor. One durable portfolio
index exposes every project, proposal, blocker, decision, and next move. Local
orchestration never implies editorial acceptance or an external-effect
authority.

## Remaining acceptance gate

Scheduler v2 migration, behavioral tests, and non-destructive static-source
installation are complete. Do not claim the whole operational redesign live-
accepted until a two-project canary proves that B can be dispatched and
completed while A remains delayed. The canonical portfolio index, full
Supervisor disposition history, and interrupted multi-route recovery also
require behavior-level verification, not Prompt phrase checks.

For the context-frontier slice, source acceptance requires the seven-project
generic canary, stale cross-lane/result quarantine, v4 portfolio consistency,
full regression, and source/installed static parity. Live registered projects
remain reconciliation-only until the exact primary Coordinator applies current
context events; this repair task does not infer or initialize them.

For the root-loop correction, live acceptance additionally requires the exact
primary Coordinator to claim and send the corrected Supervisor routes, then
apply at least one typed reconciliation result so the corresponding ledger
advances. A read-only plan showing claimable routes proves repair readiness but
is not itself proof that the loop is in flight.

## Acceptance state

- 2026-08-02 root-loop correction installed: frontier and project-context
  reconciliation are exact Supervisor external routes, standalone context
  results have a replay-safe primary-only reducer, and legacy local abstention
  completions cannot suppress the routed identities. The revision-194 live
  state was read only through the corrected source and exposed four claimable
  routes with execution `READY` while preserving the FastFictionFactory user
  card. Live restart then exposed and corrected a second blocker: the claim
  guard accepted only the first default row instead of another exact action in
  the same highest-priority ready set. Applying the first valid typed
  ClipPipeGen result exposed and corrected a third blocker: repository-frontier
  reconciliation is Missionless, while ordinary result routes retain exact
  Mission CAS. All 186 source and installed tests passed, and the 60-file
  static allowlist passed SHA-256 readback without synchronizing live `state/`.
  Three scoped routes are durably in flight; retry of the same ClipPipeGen
  result and the resulting ledger advance remain the live gate.
- 2026-08-02 WAITING_USER renderer correction: project-context migration now
  preserves user/external waits, parks, blockers, and terminal states while
  gating only ordinary `RUNNING`/`READY` work. The revision-194-shaped
  synthetic regression and all 180 source/installed tests passed; the static
  allowlist passed SHA-256 readback without synchronizing live `state/`. The
  exact primary Coordinator must regenerate the live portfolio from revision
  194 before the checkpoint is publishable.
- 2026-08-02 project-context frontier installed checkpoint: the generic
  seven-project canary covers four existing-project migration shapes plus new
  web, game, and media registrations through one contract. All 179 source and
  installed regression tests passed, and the 59-file static allowlist passed
  SHA-256 readback. Live `state/` and product repositories were not copied or
  mutated. The four live projects remain explicitly reconciliation-only until
  the exact primary Coordinator records current frontier and context events;
  no current product artifact is inferred from the former portfolio.
- 2026-08-02 cross-host static checkpoint: the 47-file allowlist passed SHA-256
  source/installed parity and all 151 tests passed in both copies. The
  portability contract keeps the recovery heartbeat prompt portable but makes
  its automation ID, target task, schedule/status, and live state host-local;
  the post-work reflection profile likewise requires an existing exact remote,
  matching upstream, and per-host authorization gate.
- 2026-08-02 corrective runtime installed: the 43-file static allowlist passed
  SHA-256 parity and all 147 source and installed regression tests. With repair
  task actor `019fbe1d-7ad7-7b63-b4db-6b0ca1385b47`, installed planning remained
  readable, reported primary writer
  `019fb3c8-2362-79d0-8640-3ec03b941e0d`, and a dry-run live event mutation was
  rejected `READ_ONLY_NON_COORDINATOR_TASK`; the Coordinator-state SHA-256 was
  unchanged.
- Previously verified baseline: deterministic single-claim plan, prepared
  delivery outbox, semantic action deduplication, event inbox, successor
  identity, changed-only BLOCKED recovery, and 72 regression tests.
- Implemented and installed: scheduler-claim/route-lease separation, three-
  route capacity, one execution route per repository, v1 state migration,
  portfolio Prompt and recovery-lease contracts, bounded-wait checkpoint
  gating, durable equal-priority round-robin admission, status-triggered
  result consumption, required handoff-chain projection, duplicate exact-route
  suppression, cursor-complete checkpoint gating, deterministic graphical
  portfolio rendering, and complete blocker-contract enforcement/reconstruction.
  That checkpoint added compare-and-swap blocker-contract revision with
  exact Supervisor-evidence binding, Mission-file locking, durable bidirectional
  history, conflict rejection, and file-level replay idempotency so later
  evidence cannot diverge from scheduler state. At that checkpoint, all 128
  regression tests passed in both source and installed copies after the
  2026-08-01 static synchronization.
- Live migration verified: the two legacy BLOCKED records now carry
  evidence-bound v2 contracts, and the canonical schema-v2 portfolio index was
  regenerated by the installed renderer without starting new work, probing
  either blocker, or rearming recovery. The later NLMYTGen blocker revision is
  additionally bound to its exact Supervisor event and evidence hash; replay is
  confirmed as a no-op.
- Live route-isolation evidence: three exact routes coexisted at scheduler
  revision 118. The Residual Atlas Supervisor successor result was consumed and
  replaced by a new exact Worker route at revision 122 while the NLMYTGen and
  FastFictionFactory action/token/cursor identities remained unchanged. The
  canonical JSON and generated Markdown were rebuilt from that same revision;
  the installed consistency gate accepted all three structured route records.
  The immutable point-in-time identities are recorded in
  [live-route-isolation-rev118-to-rev122.md](evidence/live-route-isolation-rev118-to-rev122.md).
- Live status-boundary handoff evidence: both the FastFictionFactory and
  Residual Atlas Worker Reports were consumed and sent to their distinct
  Supervisors before the revision-135 checkpoint. The plan kept both exact
  delivery identities and classified the regular ChatGPT recipients as
  `chatgpt_poll`; neither was passed to the Codex-only wait API. Both verdicts
  arrived independently. See
  [runtime-recovery-and-route-observer-gap-20260801.md](evidence/runtime-recovery-and-route-observer-gap-20260801.md).
- Intentionally inactive while idle: recovery automation.
- Still required before claiming complete live acceptance: execution and
  exact receipt adjudication of the newly typed NLMYTGen runtime recovery, plus
  one deliberately interrupted typed-recovery canary. The independent project
  completion, status-request handoff, route-admission, replacement, and durable
  portfolio/input-projection portions are now proven.
- Optional later hardening: an external distributed transaction journal if
  writers on multiple hosts or non-lock-preserving storage are ever authorized.
- Cross-host static-source readiness is implemented locally. A genuine second-
  computer bootstrap remains the acceptance canary; it must use a fresh local
  primary binding and paused recovery heartbeat and must not claim in-flight
  state migration.
