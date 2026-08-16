# v2 retirement record

The v2 architecture used one global Coordinator, Web Supervisors, persistent Workers, scheduler state, route leases, recovery leases, migration schemas, and terminal packets. It was removed from the active branch because it consumed substantial control-plane work while product projects remained stalled.

Historical source and evidence remain available at commit:

```text
c82b88f80c8e595d2ff6303c65bf54aadab15035
```

Do not install or reconstruct that revision as current runtime. It is retained solely for audit and rollback analysis.
