# Monotonic artifact frontier reconciliation

## Guarantee

The Coordinator must not select ordinary work, request a generic successor,
present a review card, or promote a portfolio row until it has a current
`FrontierCertificate` for the exact repository and supervision lane. The
certificate binds the artifact identity, frontier epoch/event, branch and
HEAD observation, disposition, source actor, and the complete authority-signal
fingerprint.

The authoritative state is `frontier-ledger.v1.json`; chat recency, filenames,
Mission timestamps, and delivery acknowledgements are not frontier evidence.
The repository copy is static source only. The installed skill owns the only
live `state/`; source tests and repair work must not initialize or copy live
frontier state.

## Monotonic reducer

Each `FrontierRecord` is a compare-and-swap event:

- `frontier_epoch` must equal `based_on_frontier_epoch + 1`;
- the based-on epoch must equal the current lane epoch;
- changing artifacts must explicitly supersede the prior event;
- source precedence is `human > supervisor > worker > repo_observation > coordinator`;
- a lower-precedence source cannot replace a higher-precedence current record;
- rejected, superseded, and parked artifacts are retained as tombstones and
  cannot be re-promoted by a lower-precedence source;
- exact event and result replay is idempotent; conflicting replay fails closed.

No record is inferred when legacy state lacks lineage. It remains
`legacy_unverified` and produces a reconciliation action.

A retired artifact is reviewable only when a `present_user_card` action sets
`historical_review=true` and supplies its exact artifact ID, revision, and
SHA-256. This opt-in binds the card to the matching tombstone while the
certificate continues to bind the current frontier. It does not mutate or
re-promote the retired artifact; promotion still requires a separate valid
authority event through the monotonic reducer.

## Temporary safety mode

Missing or stale certificates activate `TRANSPORT_ONLY_RECONCILIATION`.
Existing exact route observation, direction/question/user-response transport,
and frontier/authority reconciliation may continue. Ordinary Mission advance,
new discretionary work, generic successor requests, review presentation, and
portfolio promotion do not continue. After all registered repositories have
verified records, the ledger reports `FRONTIER_VERIFIED`.

Gate abstention is machine-readable: an explicit null frontier returns
`NO_ACTIVE_CANDIDATE`, missing or unverified authority returns
`AUTHORITY_CONFLICT`, other unresolved lineage returns
`FRONTIER_RECONCILIATION_REQUIRED`, and a late external result returns
`STALE_RESULT_QUARANTINED` while remaining auditable.

## Authority high-water signal

`collect_authority_signals` records every registered repository even when no
file watch is configured. The signal includes branch, HEAD, upstream parity,
dirty paths, in-progress Git operation, commit count/time, and for each
configured authority file its content hash, indexed blob, last commit, mtime,
and dirty state. A certificate is invalid when its branch, HEAD, or complete
authority fingerprint no longer matches. External results cannot authorize
themselves: their complete authority signal must equal the Coordinator's
independently collected current signal. An unavailable Git root, invalid HEAD,
missing configured source, invented revision, or obsolete authority path
cannot issue a certificate.

## External result lifecycle

Every action with `requires_external_result=true` follows:

`created -> dispatched -> delivery_acknowledged -> result_received -> result_parsed -> result_validated -> result_applied`

`failed`, `stale_result_quarantined`, and `cancelled` are terminal alternatives.
An arriving result may prove delivery and move directly from `dispatched` to
`result_received`, but delivery alone never completes semantic work.

The result binds the exact action, recipient thread, turn, message, result ID,
repository/lane, disposition, based-on frontier epoch, FrontierRecord,
authority high-water signal, and before/after Mission state. The application
transaction validates all identities, applies the frontier CAS, replaces the
exact Mission, completes the scheduler route, and regenerates portfolio v3.
Only then is the action `result_applied`.
Failed and stale results close their route as a terminal non-applied action,
remain in the failure/quarantine ledger, and regenerate the portfolio without
leaving a false active wait.

## Persistence and replay

`coordinator-action-apply-result` writes a prepared transaction journal before
replacing frontier, Mission, scheduler, and portfolio files. Each file replace
is atomic. If execution stops between files, replaying the same action/result
reads the journal and writes the exact desired snapshots again; a different
payload under the same result identity is rejected.

Portfolio v3 recomputes `semantic_fingerprint` from content and records the
frontier ledger revision, safety mode, certificate, epoch, disposition, and
active artifact for every row. A syntactically valid but stale projection is
rejected.

## Read-only audit

Use:

```powershell
python scripts/supervise_repo_loop.py frontier-audit --dry-run
```

The audit does not write. It reports unverified repositories/lanes, invalid
branch or HEAD bindings, missing authority signals, pending unapplied external
routes, quarantined results, and scheduler/frontier/portfolio projection
differences. Each finding describes the evidence required by a recommended
reconciliation event but does not create that event, a Mission, or a review
card. Reconciliation must use current Git and authority evidence; it must not
choose the newest-looking historical artifact.
