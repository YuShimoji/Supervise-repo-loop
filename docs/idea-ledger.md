# Idea ledger

## Active ideas

- Add a transaction journal if multiple Coordinator writers are ever allowed.
  Current authority permits one user-facing Coordinator and therefore one
  state writer.
- Add exact thread cursors to the adapter snapshot so every legacy inferred
  wait can migrate to a first-class send receipt.
- After durable state proves sufficient, consider rotating the very large
  historical Coordinator task to a fresh task to reduce ordinary interactive
  context cost. Task creation remains a separate user-authorized action.

## Rejected directions

- Batch all review cards after every project reaches terminal state: rejected
  because it creates head-of-line blocking and hides completed user work.
- Infer review weight from terminal state: rejected because gate necessity and
  review depth are separate concerns.
- Send a user reply directly to a Worker: rejected because the exact Supervisor
  must classify the response and preserve authority.
- Rewrite historical terminal Missions to repair the inbox: rejected because
  historical state is audit evidence.
- Keep a periodic Codex heartbeat active and run a cheap probe first: rejected
  because the model has already started before the probe runs.
- Treat `allow_request_next_mission=true` as a perpetual queue item: rejected
  because it repeats no-work requests and can starve later repositories.
