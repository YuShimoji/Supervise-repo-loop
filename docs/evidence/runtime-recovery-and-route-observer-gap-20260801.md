# Runtime-recovery and route-observer gap evidence — 2026-08-01

## Scope

This record captures two live defects found after scheduler v2 deployment and
the bounded corrections required before another Coordinator recovery pass. It
is evidence of the control-loop problem, not evidence that NLMYTGen product
generation, YMM4 execution, publication, or human acceptance has succeeded.

## Defect 1: an adopted NLMYTGen repair had no executable action

The exact NLMYTGen video Supervisor returned `ADOPTED` for authorization
`CODEX-SANDBOX-DENY-READ-STATE-REVERSIBLE-REINITIALIZATION-V1` and resulting
action
`NLMYTGEN-CODEX-SANDBOX-DENY-READ-RECOVERY-20260801-01`. The decision is bound
to event
`528b175885a1fd4eee1f42dcb6fecc0ef11b7fe598a00bda751af2547527babf`.

The event fixed all of the following boundaries:

- exact target
  `C:\Users\thank\.codex\.sandbox\deny_read_acl_state.json`;
- expected pre-state: 22 NUL bytes with SHA-256
  `6a4875ddaceaa91fb3369f0f6d962f77442daf1b1d97733457d12bcabdf79441`;
- byte-exact backup, non-destructive quarantine, one normal helper
  regeneration, read-only postcheck, and preserved rollback only;
- the same persistent Worker
  `019fb098-4cdb-7c10-8794-28269b92276d` on the exact Thank host;
- Probe A in `C:\tmp`, Probe B in the exact repository, then the existing
  read-only runtime doctor;
- no ACL, ownership, OS-policy, execution-policy, YMM4 UI, render, candidate,
  publication, or Git authority.

The old scheduler consumed the Supervisor route but had no typed action model
for this adopted operation. It therefore displayed NLMYTGen as blocked even
though neither the user nor the Supervisor owed another input. This was a
scheduler expressiveness defect, not a missing-authority condition.

## Defect 2: Supervisor and Worker routes used one observation API

Active routes mixed regular ChatGPT Supervisor chats with Codex Worker tasks,
while the recovery Prompt attempted to put every recipient into
`wait_threads`. `wait_threads` observes Codex tasks, not ChatGPT chats. A
FastFictionFactory Supervisor response was already complete but remained
leased until a later targeted poll consumed it.

The corrected plan persists and verifies one observer per exact recipient:

| Recipient | Observer | Required identity |
|---|---|---|
| Codex Worker | `codex_wait` | app host ID, task ID, cursor |
| ChatGPT Supervisor | `chatgpt_poll` | chat ID, cursor |

Missing, unknown, or adapter-mismatched transport is a protocol error. It may
not silently default to either observer.

## Live isolation readback

At scheduler revision 122, Residual Atlas, NLMYTGen, and FastFictionFactory had
three independent route leases. By revision 127:

- the NLMYTGen `ADOPTED` control result had been consumed without changing the
  Residual Atlas route;
- the previously completed FastFictionFactory Supervisor result had been
  consumed;
- FastFictionFactory Mission `fff-development-densou-source-packet-v1@1` had
  been materialized and dispatched to its existing Worker as action
  `3c2d11e94de65f0da61de233d8e58455`;
- the Residual Atlas Worker route remained action
  `64e9d536a00957e4f34b121cc2b8495d`, with its delivery token, payload hash,
  recipient, and cursor unchanged.

This proves that one delayed project did not prevent another project from
receiving a new Work Order. It also exposed the observation bug because the
FastFictionFactory result was available before the old recovery pass consumed
it.

## Worker reports drained at the next status boundary

Both live Worker routes subsequently completed:

- Residual Atlas produced the one-command, relocatable local review artifact
  Worker Report.
- FastFictionFactory returned a valid stop because no exact Densou source body
  or unique source locator existed in the authorized delivery surface.

The next Coordinator pass consumed both exact Worker Reports and sent each one
to its own Supervisor before producing the portfolio checkpoint. At scheduler
revision 135 the two independent ChatGPT routes were:

- FastFictionFactory action `d820271efcf2d6520acbc41790c435bf` to
  Supervisor `6a51114d-ed78-83e8-966f-058d37d010af`;
- Residual Atlas action `6c80268beda25689a13c51e3f630adf2` to
  Supervisor `6a6caa7f-2400-83ee-97f1-a9f7a3592f58`.

The plan preserved both delivery tokens and cursors, classified both
recipients as `chatgpt_poll`, exposed no Codex `wait_targets`, and allowed a
checkpoint only after those same-pass handoffs. Both Supervisor results then
arrived: FastFictionFactory returned an exact `USER_ACTION` source-delivery
card, while Residual Atlas returned terminal `COMPLETE / ADVANCED`. This is the
status-request handoff case that the earlier loop failed; the remaining
acceptance step is exact idempotent result consumption under the installed
transport-aware runtime.

## Required implementation properties

The typed runtime action must be more restrictive than free-form shell work:

1. bind the trusted Supervisor event, scheduler delivery, exact threads, host,
   target bytes, and fixed handler;
2. persist a pre-effect intent before the first runtime-state mutation;
3. keep every consumed phase recovery-owned until an exact route or safe
   terminal result exists;
4. reject stale backup/quarantine pairs, arbitrary success strings, incomplete
   Worker receipts, and unbound Supervisor results;
5. preserve full probe and regenerated-state evidence through rollback and
   Supervisor adjudication;
6. make exact replay a true no-op and reconcile one-file-ahead crash states
   without a second repair attempt.
