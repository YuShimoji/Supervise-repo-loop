# Development and verification

This repository has one maintained execution path:

```powershell
pwsh -NoProfile -File tests/Test-BootstrapContract.ps1
pwsh -NoProfile -File scripts/bootstrap-frontier-loop.ps1 -StorageRoot <absolute Storage root>
```

The first command is a source-contract check. The second is the behavioral clean-host path and can build FrontierBoard, so use isolated profile and install roots in automated acceptance.

Acceptance requires all of the following:

- the compatibility source contains no active v2 scheduler implementation;
- the canonical Coordinator remote contains the minimum tested revision or a descendant;
- the checkout is clean, on `main`, tracks `origin/main`, and can update by fast-forward only;
- the Coordinator-owned setup reports the locked FrontierBoard revision, installed executable canary, Core/skill parity, and no legacy state restoration;
- a second setup can reuse the verified host-local receipt;
- dirty local residue is preserved and produces HOLD rather than cleanup.

Passing control-plane tests is not content-project progress or creative acceptance.

