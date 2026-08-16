# Project context

## Current direction

The repository is a backward-compatible public doorway. It bootstraps the canonical simplified loop from `project-reflection-coordinator` and does not own runtime scheduling.

## Current verified floor

- Coordinator minimum revision: `886deb2b3593070f9e6d0918c373af41004e181f`
- Core: `10.2-frontier.1`
- Core SHA-256: `BB6CC6421676E4DFB61C67197ECA7C760C45F666E9143CE6BA7AADA579DA5E28`
- FrontierBoard: `main@29ac4aa1e478ac028716b08ea5526ae18cf381ad`, app `0.4.1`, 54 tests

Descendants of the Coordinator floor are intentionally allowed. This lets Planner007 receive future accepted Core and Board lock changes through normal runtime sync without publishing a new compatibility commit for every update.

## Safety boundary

Bootstrap creates host-local profile, Board data, installed app, receipt, Core, and thin-skill copies only after canonical verification. It does not copy task IDs or old portfolio state, and it does not start music/video production.
