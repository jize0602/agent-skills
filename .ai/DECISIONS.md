# Decisions

- The canonical distribution repository is Public `jize0602/agent-skills`, verified by GitHub connector. Both public skills share the static version in `VERSION.json`; `agent-skills-project` is internal maintenance context.
- `.ai/CONTINUITY.json` uses schema 1 and maps all contract roles. `auto_sync: true` records the requested scoped task-sync intent; it does not grant deployment or credential authority.
- `github-bootstrap` is a thin installation and authorization layer. The existing continuity `context.py` is a separate local project-brain initializer/validator. GitHub actions require a separately usable official connector or CLI.
- Browser sign-in is not treated as connector/CLI authorization. When authorization is missing, the user completes the official authorization flow; the same pending intent is resumed afterward.
- A single 404 or an App-scoped repository listing cannot prove absence of a private repository. Creation is possible only after independent authoritative absence evidence and user confirmation.
- The official GitHub repository and pinned release tag/commit are the source trust root; a same-repo SHA manifest protects downloaded bytes but is not a cryptographic publisher signature.
- CI is limited to tests, continuity/Skill-manifest validation, and a full-history secret scan with read-only repository permission.
- Formal Skill version `v1.0.0` is distinct from ordinary continuity backup Releases. The formal tag remains pinned to its CI-tested source commit; later project-brain evidence commits on `main` do not silently move the tag.
- The backup snapshot cannot include its own post-upload verification receipt. Store that receipt outside the immutable snapshot on GitHub and check live Release assets for current backup status.
- Do not rewrite or move `v1.0.0` to hide stale candidate wording. Release a patch version with static version data and live GitHub Release/CI checks, preserving immutable history and the new Agent's discovery as evidence.
