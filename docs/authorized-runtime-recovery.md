# Authorized runtime recovery actions

## Why this exists

A Supervisor may decide that a blocked project has enough authority for one
specific runtime repair. That decision is not itself execution. The Coordinator
must convert it into a durable, claimable action; otherwise the project appears
to be waiting even though neither the user nor the Supervisor has anything left
to provide.

Runtime recovery is therefore a typed subflow of the portfolio scheduler, not
free-form shell work and not a generic retry of a `BLOCKED` Mission.

```text
Supervisor disposition ADOPTED
  -> authorized action registered
  -> exact local repair claimed
  -> byte-exact repair receipt persisted
  -> exact persistent Worker restricted probe
  -> rollback when the authorized failure rule requires it
  -> exact Supervisor receipt adjudication
  -> product Mission may be reselected only after that verdict
```

## Authority boundary

Every action binds all of the following before it becomes `READY`:

- repository, Mission, attempt, lane, Supervisor, Worker, and host identities;
- the Supervisor decision event and the SHA-256 of its evidence;
- an explicit authorization ID and a stable runtime action ID;
- one allowlisted handler ID;
- the exact target and its expected pre-repair byte size and SHA-256;
- deterministic backup, quarantine, and recovery-receipt locations;
- the runtime-owner maintenance surface used only for the exact local repair;
- the distinct restricted workspace-write surface used only for the same-Worker
  probes, plus the stop-on-first-failure policy.

An action may not contain an arbitrary command or an arbitrary target. Unknown
handlers, paths outside the handler's fixed boundary, evidence mismatches, and
precondition mismatches fail before runtime state is changed.

The repair surface and probe surface are separate evidence fields. A receipt
must not describe the runtime-owner repair as a restricted Worker probe, and a
probe must not use the maintenance or danger-full-access surface. Registration
also verifies that the trusted Supervisor disposition names both boundaries;
the split is not inferred later from the action kind.

## One-shot effect lifecycle

The effect ledger is durable and crash-resumable:

```text
AUTHORIZED
  -> EFFECT_INTENT
  -> EFFECT_PREPARED
  -> REPAIR_PREPARED
  -> RESULT_READY
  -> COMPLETE

REPAIR_PREPARED
  -> ROLLBACK_REQUIRED
  -> RESULT_READY
```

`EFFECT_INTENT` records durable intent before the first target-side effect. It
is unconsumed but recovery-owned and cannot be released as an ordinary local
action. The first successful byte-exact backup moves the ledger to
`EFFECT_PREPARED` and consumes the one-shot authority. `REPAIR_PREPARED` means
the original target is preserved and the exact Worker probe is the next
transition. Completion requires a structured receipt identity. Replaying the
same action and receipt is a no-op; a conflicting replay is rejected.

The allowlisted repair is idempotent across process interruption. Existing
backup and quarantine files may be reused only when their byte identity equals
the authorized original. A precondition mismatch leaves the authority
unconsumed. Once the first permitted effect succeeds, the one-shot authority
is consumed regardless of the later probe result.

## Portfolio presentation

The normal seven-stage project graph remains the common user view. Runtime
recovery uses stage labels rather than inventing a second visual language:

| Common stage | Runtime-recovery label |
|---|---|
| Mission | Blocker diagnosed |
| Work Order | Repair authority adopted |
| Worker | Runtime repair / restricted probes |
| Worker Report | Recovery receipt |
| Supervisor | Receipt adjudication |
| Verdict | Resume or bounded stop |
| Next Route | Product Mission selection |

The project row must show the exact current phase, why that phase became
necessary, who owns it, what evidence introduced it, and the next permitted
transition. `AUTHORIZED` or `READY` is not `RUNNING`; only a claimed local
effect or sent exact route is in progress.

## Recovery automation

A runtime ledger is a recovery lease from `EFFECT_INTENT` through
`EFFECT_PREPARED`, `REPAIR_PREPARED`, `ROLLBACK_REQUIRED`, and `RESULT_READY`.
It must wake crash recovery until it reaches `COMPLETE` or an exact external
route owns the next transition. An ordinary unclaimed `AUTHORIZED` record does
not justify a periodic model wake. Unrelated repositories retain their route
leases and remain schedulable throughout this subflow.
