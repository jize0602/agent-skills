# Decisions

- The canonical distribution repository is Public `jize0602/agent-skills`, verified by GitHub connector. The two public skills are `github-bootstrap` and `github-continuity` at version `1.0.0`; `agent-skills-project` is internal maintenance context.
- `.ai/CONTINUITY.json` uses schema 1 and maps all contract roles. `auto_sync: true` records the requested scoped task-sync intent; it does not grant deployment or credential authority.
- `github-bootstrap` is a thin installation and authorization layer. The existing continuity `context.py` is a separate local project-brain initializer/validator. GitHub actions require a separately usable official connector or CLI.
- Browser sign-in is not treated as connector/CLI authorization. When authorization is missing, the user completes the official authorization flow; the same pending intent is resumed afterward.
- A single 404 or an App-scoped repository listing cannot prove absence of a private repository. Creation is possible only after independent authoritative absence evidence and user confirmation.
- The official GitHub repository and pinned release tag/commit are the source trust root; a same-repo SHA manifest protects downloaded bytes but is not a cryptographic publisher signature.
- CI is limited to tests, continuity/Skill-manifest validation, and a full-history secret scan with read-only repository permission.
- The current Skill release and integration acceptance remain pending until their remote gates pass.
