# Implementation

Status: public `v1.0.0` baseline released; version 1.0.1 corrects transient status records; final release and first backup pending.

- R1: `skills/github-continuity/` copies the previously accepted generic Skill. Only the public-receipt gate and test-fixture literals changed; Private behavior remains backward compatible. `skills/github-bootstrap/` is a thin new layer, not a continuity replacement.
- R2: `scripts/release_manifest.py` hashes both Skills under the official repository/tag identity; `SKILL_MANIFEST.json` records exact file hashes. `skills/github-bootstrap/scripts/install.py` checks the complete set before non-overwriting installation. The manifest is not independent publisher authentication: the Agent must first verify the official repository and tag/commit.
- R3/R4: `scripts/auth.py` classifies evidence without credentials or network side effects, returns a preserved intent and supports `resume` after fresh official authorization. A private 404 or App-scoped listing absence never proves repository absence.
- R5: `.ai/CONTINUITY.json` maps project facts with `auto_sync=true`; `AGENTS.md` requires test, scoped sync, CI and exact remote readback at completion. `.github/workflows/ci.yml` is read-only and has no deploy step.
- R6: Historical Public Release and clean remote install passed. Independent GitHub-only Luna Max recovery passed 20/20. The immutable `v1.0.0` tag still contains stale candidate wording, so version 1.0.1 removes transient publication claims from `VERSION.json`; its release and first backup remain separate acceptance gates. See [`ACCEPTANCE.md`](../ACCEPTANCE.md) before claiming final PASS.

Requirements tracking pointer: `.ai/REQUIREMENTS.md` ([open](REQUIREMENTS.md))
