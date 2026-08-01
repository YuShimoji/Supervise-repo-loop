# Live route-isolation evidence: scheduler revisions 118 to 122

## Scope

This is a point-in-time readback of the installed Coordinator on 2026-08-01
(Asia/Tokyo). It records one real result-consumption and route-replacement
transition while two unrelated external routes stayed leased. It does not claim
project completion, a status-request Worker-Report canary, or interrupted
recovery acceptance.

## Before: revision 118

The scheduler had no global claim and three `waiting` route leases:

| Repository | Action | Recipient | Delivery token | Cursor |
|---|---|---|---|---|
| Residual Atlas | `6532058aa2839d28ac44693de9848b2d` | `6a6caa7f-2400-83ee-97f1-a9f7a3592f58` | `82638c7e31d31eab5f35bffe3a67793b84519f56e3332cea1dcc75b42ab554e9` | `735d6fb9-a41f-46cd-be26-c959bfa43ccc` |
| NLMYTGen | `a3270af7d986ceadb7a6d34c37486a12` | `6a6625d6-a82c-83e8-87c3-48db581b6c5d` | `ec9b367ccd16a04731e51e6d256e0a8f2744170ef15b4de080fe2bbb678e443a` | `da052df0-54f5-4e69-9134-a1d7b184f91f` |
| FastFictionFactory | `57341d320a6cd210ac11f05810ede371` | `6a51114d-ed78-83e8-966f-058d37d010af` | `fd8a5af889098591b77ce3717558dde6d44c6dc655219965eca1387673ba7674` | `1e6ff1a8-d155-4b88-8048-160ca73734aa` |

Residual Atlas was waiting for the exact Supervisor's successor result. The
other two routes carried the user's bounded NLMYTGen runtime-repair authority
and FastFictionFactory source-authority declaration to their exact Supervisors.

## Observed transition

The Coordinator consumed the Residual Atlas successor response exactly once,
persisted Mission
`residual-atlas-one-command-first-playable-review-access-v1@1`, and dispatched
its compact Work Order to the existing persistent Worker. It did not complete,
release, resend, or regenerate either unrelated control route.

## After: revision 122

The scheduler again had no global claim and three `waiting` route leases:

| Repository | Action | Recipient | Delivery token | Cursor | Result |
|---|---|---|---|---|---|
| Residual Atlas | `64e9d536a00957e4f34b121cc2b8495d` | `019fa98c-5a12-7362-90cb-0870698e14d4` | `a919aecc176ba7f0dd942587d64ac39d32f81151fb1d9413bae1ec381f3a5180` | `019fbda8-05f0-7a03-a8a6-822c797b4d4c` | Replaced by the distinct Worker route |
| NLMYTGen | `a3270af7d986ceadb7a6d34c37486a12` | `6a6625d6-a82c-83e8-87c3-48db581b6c5d` | `ec9b367ccd16a04731e51e6d256e0a8f2744170ef15b4de080fe2bbb678e443a` | `da052df0-54f5-4e69-9134-a1d7b184f91f` | All identities unchanged |
| FastFictionFactory | `57341d320a6cd210ac11f05810ede371` | `6a51114d-ed78-83e8-966f-058d37d010af` | `fd8a5af889098591b77ce3717558dde6d44c6dc655219965eca1387673ba7674` | `1e6ff1a8-d155-4b88-8048-160ca73734aa` | All identities unchanged |

The canonical portfolio JSON recorded scheduler revision `122`, three
structured active routes, and semantic fingerprint
`e3206c39dc787b04ea01da7ecff3d2fe3460ef44176fcd8763f3aa9f898d9d4f`.
The installed `portfolio-render --dry-run` accepted the exact scheduler/JSON
set, and the generated Markdown matched its canonical file.

## Acceptance meaning

This proves live route coexistence, isolated result consumption, successor
materialization, Worker dispatch, and portfolio projection consistency. Still
required for whole-loop live acceptance:

1. one project reaches completion while another remains deliberately delayed;
2. a Worker Report already present at a normal status request is handed to its
   exact Supervisor before the answer checkpoints;
3. interruption and recovery preserve every surviving route identity without a
   duplicate send.
