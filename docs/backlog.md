# supervise-repo-loop backlog

This is the durable queue for incomplete work that must survive task handoff.
It does not grant live-state authority. Static implementation items are owned
here; portfolio execution items remain owned by the one bound primary
Coordinator and require the deterministic Mission value gate before dispatch.

## Active implementation queue

| ID | Purpose | Effect | Requirements | State | Owner | Next move |
|---|---|---|---|---|---|---|
| B-001 | Enforce one live-state writer | Prevent repair, audit, and reporting tasks from becoming a second Coordinator | Exact `CODEX_THREAD_ID` binding, canonical installed state paths, idle-only explicit rebind, negative tests | completed | supervise-repo-loop source | Installed-runtime proof is tracked by B-006 |
| B-002 | Gate legacy Mission dispatch | Prevent an old `WORK_ORDER_RECEIVED` or early pending Mission without a value contract from reaching a Worker | Scheduler-level admission action, corrected-contract route, replay-safe contract attachment | completed | supervise-repo-loop source | Installed-runtime proof is tracked by B-006 |
| B-003 | Align Prompt, schema, and runtime value fields | Keep quick-win decisions deterministic instead of prose-only | Exact `value_contract_v1` field list and admission behavior in the machine-readable Prompt and schemas | completed | supervise-repo-loop source | Installed-runtime proof is tracked by B-006 |
| B-004 | Cover live mutation entry points | Prevent migration, compile, render, event, runtime, and route commands from bypassing the writer fence | Secondary-task negative tests and canonical-path assertions | completed | supervise-repo-loop source | Installed-runtime proof is tracked by B-006 |
| B-005 | Make user action and roadmap status complete | Show whether the user must act, all instructions in one place, and each block's position in the project path | One top-level user card, per-project roadmap, deterministic renderer and validation | completed | supervise-repo-loop source | Primary must regenerate the live projection after B-006 |
| B-006 | Deploy and prove the corrected runtime | Move static changes to the installed skill without touching live state from this repair task | Green source tests, diff check, idle primary, allowlisted sync, installed tests, secondary live rejection check | completed | supervise-repo-loop source | Primary must reread the installed contract and regenerate its live portfolio |
| B-011 | Make the development and Codex scheduling structure portable | Let another computer reproduce the static runtime without cloning writer identity or host-local automation state | Portable manifest, exact remote/upstream check, static install parity check, ignored local tool state, cross-host runbook | completed | supervise-repo-loop source | Continue with the genuine receiving-host canary in B-012 |
| B-012 | Prove a second-host bootstrap | Confirm the documented boundary on a genuinely separate Codex installation | Fresh clone, new host-local installed skill, new exact primary binding, paused recovery heartbeat, no copied `state/` or task ID | waiting_external | User / receiving-host operator | Run the host checklist on the receiving computer; do not move an in-flight route as part of this canary |

## Primary Coordinator execution queue

These items are not authorization to start work. After B-006, the bound
primary Coordinator must re-read the installed contract and admit only an
exact `value_contract_v1` quick win. `NO_WORK` or parking is valid.

| ID | Purpose | Effect | Requirements | State | Owner | Next move |
|---|---|---|---|---|---|---|
| B-007 | Reconcile ClipPipeGen to its current gate | Return from chronology drift to the current S1/S4 review path | Current authority fingerprint, exact current next action, existing-artifact reuse path | waiting_user | User / S4 common-context review owner | Review only `clip-s1-two-source-common-context-probe-v1-001` and reply `accept`, `bounded_repair`, `reject`, or `entrypoint unavailable` through the Coordinator |
| B-008 | Reconcile FastFictionFactory to its current gate | Return from benchmark/Densou drift to the current D0 review path | Current authority fingerprint, exact review consumer, no new source without explicit user authority | parked_pending_exact_supervisor_event | exact FastFictionFactory Supervisor | One poll was unchanged; do not resume Densou. Consume only a later semantic result for the existing D0 review gate |
| B-009 | Reconcile NLMYTGen to its current gate | Keep runtime recovery bounded to the named terminal/YMM4 evidence path | Completed runtime ledger evidence, exact current blocker, smallest non-repeating trace or E2E canary | parked_pending_exact_supervisor_event | exact NLMYTGen video Supervisor | One poll produced no new disposition; do not repair, probe, dispatch a Worker, or start YMM4. Consume only a later semantic result |
| B-010 | Select Residual Atlas's next value move | Avoid creating a successor merely because the prior Mission completed | Current authority next action, direct gate delta, one-turn existing-artifact reuse by default | admitted_dispatched_waiting_supervisor | exact Residual Atlas Supervisor | Consume action `c7b4ba11eda48e9f4a95e9ee0d7a2dbd` after cursor `86d250d4-efce-4cad-892c-459e924452ed`; accept only a complete value contract, `NO_WORK`, or a complete user card |
