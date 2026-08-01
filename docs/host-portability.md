# Cross-host development and Codex scheduling

## Guarantee and boundary

The Git repository is a portable development checkpoint. A clone contains the
static skill, scheduler implementation, schemas, tests, documentation, and the
machine-readable automation profiles in
`references/automation-portability.v1.json`.

The following are deliberately not portable through Git:

- the installed skill's live `state/`;
- Codex automation records and automation IDs;
- the primary Coordinator task ID and `CODEX_THREAD_ID` binding;
- host registry roots, credentials, one-time gates, helper paths, caches, and
  automation checkpoints;
- `.serena/`, `.playwright-mcp/`, ignored media, and other tool-local state.

This boundary transfers development structure and scheduling contracts. It is
not an in-flight Mission, delivery token, route lease, or live-state migration.
Do not run two hosts as writers for one live Coordinator state tree.

## Stable Git handoff

The remote `master` branch is the stable base. Work that is not yet accepted is
published on a named branch with an upstream and a draft pull request. A green
branch proves a reviewable checkpoint; it does not imply merge, live
acceptance, release, or deployment.

The future post-work reflection task may push only when all of these are true:

1. `origin` resolves to the expected exact repository;
2. the current named branch tracks the same-named branch on `origin`;
3. repository rules identify the changed paths as owned and publishable;
4. the project health gate and `git diff --check` pass;
5. no tracked `state/`, `.serena/`, `.playwright-mcp/`, secret, local
   credential, or automation checkpoint enters the commit;
6. a normal push can be read back as the exact local SHA.

Remote creation, remote replacement, history rewrite, force push, merge,
release, and acceptance remain outside that task. The one-time authorization
gate, project discovery helper, and its cache/checkpoint live outside this
repository and must be configured independently on each computer.

## Receiving-computer procedure

1. Clone the exact GitHub repository and select the intended branch. Do not
   bring a copied `.git` directory or installed runtime tree from the old host.
2. Run the source check:

   ```powershell
   .\scripts\verify-portable-checkout.ps1
   ```

3. Preview and install the static skill into the receiving user's profile:

   ```powershell
   .\scripts\sync-installed-skill.ps1 -WhatIf
   .\scripts\sync-installed-skill.ps1
   ```

   The installer copies only the static allowlist and never initializes,
   copies, reads, prunes, or deletes live `state/`.
4. Create or select the one primary Coordinator task on the receiving host.
   Fresh local runtime registration follows the installed skill contract. If
   continuity from an existing live Coordinator is required, stop here: that
   is a separate administrator migration and is allowed only at a verified
   idle edge with no claim, prepared delivery, route lease, or recovery-owned
   runtime phase.
5. Create a host-local recovery heartbeat using
   `references/recovery-lease-prompt.md`. Bind it to the exact receiving-host
   primary task, start it paused, and never paste or infer the sending host's
   task ID. Activate it only after the receiving host's `coordinator-plan`
   reports `watchdog_should_be_armed=true`; pause it when the plan reports
   false.
6. Configure the post-work reflection task separately on the receiving host.
   Its project ID, schedule, one-time gate, helper path, and checkpoint are
   host-local. The repository-side prerequisites are the exact `origin`, the
   same-named upstream, and the checks above.
7. After the branch exists remotely and static installation is complete, run:

   ```powershell
   .\scripts\verify-portable-checkout.ps1 -RequireInstalledParity -RequireClean -VerifyRemoteTip
   ```

## Scheduler acceptance checks

| Surface | Expected on a receiving host | Failure response |
|---|---|---|
| Static source | Exact Git branch and green tests | Do not install or schedule |
| Installed static skill | SHA-256 parity for every allowlisted source file | Re-run the non-destructive installer |
| Live `state/` | New or explicitly administered host-local state only | Do not copy it through Git |
| Primary writer | Exact receiving-host task ID | Stay read-only; never impersonate or infer |
| Recovery heartbeat | New host-local automation, exact target, initially paused | Pause or delete the incorrect local binding |
| Post-work reflection | Exact remote/upstream plus a host-local one-time gate | Hold this repository; do not create/change remotes automatically |
| Second-host canary | Fresh clone/install/binding with no copied task ID | Keep cross-host live acceptance pending |

## Sending-computer closeout

Publishing the branch does not retire the old host. Preserve its ignored
files, installed live state, automations, and active tasks. Only the user or an
explicit administrator migration may retire or rebind them. A receiving-host
canary must avoid moving an in-flight route; proving live route transfer would
require a separate migration design and acceptance gate.
