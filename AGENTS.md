# Repository guidance

Read `docs/project-context.md` and `docs/development.md` before changing this
repository, then read the authority or specification they name for the slice.

This Git repository is the canonical static source. The installed skill is the
runtime copy and owns the only live `state/`. Never copy, initialize, delete,
or version live `state/`, and never synchronize `.serena/`. Use the repository
scripts for tests and source-to-install synchronization.

If a referenced file is missing, treat the reference as stale rather than as a
blocker. Preserve unrelated changes and report residual work with purpose,
effect, requirements, state, owner, and next move.

