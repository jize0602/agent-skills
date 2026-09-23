# Agent Instructions

- Start with `.ai/START_HERE.md` and follow the role map in `.ai/CONTINUITY.json`.
- Treat mapped files as the project brain's sources of truth. Update the relevant records when a task changes facts; do not duplicate or invent evidence.
- This is a public skills project. Do not add private project facts, customer data, credentials, or unrelated source code.
- Both public skills (`github-bootstrap` and `github-continuity`) use the versions in `VERSION.json`. Check the current formal GitHub Release and exact-commit CI, then [`ACCEPTANCE.md`](ACCEPTANCE.md), before claiming integration acceptance. Never infer live publication state from a tagged source snapshot alone.
- `.ai/CONTINUITY.json` records the requested automatic, scoped continuity sync. It does not authorize deployment or credential handling. Use only the official GitHub connector or CLI after it is actually authorized.
- A browser sign-in is not proof that the agent's GitHub connector or CLI is authorized. If official access is missing, the user completes that authorization personally. Preserve the pending sync intent and resume after access is available.
- Record tests, manifest checks, secret scans, publication, and remote state with their actual local/remote scope. Never pre-fill a success result.
- CI is read-only and has no deployment step.

<!-- github-continuity-completion-rule -->
## Continuity completion

For work in this repository, completion updates the logical files mapped by `.ai/CONTINUITY.json`. When `auto_sync` is `true`, the scoped auto-sync may commit and push only those continuity changes, then must wait for CI and read back the remote commit/tree before reporting synchronization. This rule does not authorize deployment, credential handling, or unrelated remote work.
