# Public Skills acceptance

Evidence captured 2026-09-23. This file records checks available before the version `1.0.1` release, final continuity sync and backup. The current GitHub branch, CI, Release, and backup assets must be checked live; a snapshot cannot contain its own post-upload verification receipt. Historical `v1.0.0` evidence is labeled as such and never substitutes for a newer release.

| Gate | Result | Evidence |
| --- | --- | --- |
| Official repository / visibility | PASS | `jize0602/agent-skills`, GitHub repository ID `1383044358`, `public` |
| Formal Skill Release | PASS | [`v1.0.0`](https://github.com/jize0602/agent-skills/releases/tag/v1.0.0), Release ID `394815144`, tag points to `8332b38d5f6d3fcf114bfe8e42069f37a738533b` |
| Release source CI | PASS | [run `35863797817`](https://github.com/jize0602/agent-skills/actions/runs/35863797817), exact head `8332b38d5f6d3fcf114bfe8e42069f37a738533b`, completed/success |
| Anonymous public read | PASS | Chrome Incognito displayed the tagged README with `Sign in` link and `Public` badge |
| Official tagged archive clean install | PASS | GitHub `v1.0.0` ZIP SHA-256 `ac76855af46fbf448188d686c59946622089425723a6eea6cbdf7bb79fb7a491`; manifest SHA-256 `77f431c1462ca01f8e240168439c8b23257e9115122944698a524c9024dac0e6`; installed both Skills into an empty temporary destination with exact-file/sha/version verification and no diff from source |
| Tests on downloaded source | PASS | 34 bootstrap, 61 continuity, 6 release-manifest tests; 101/101 total; `security_audit=PASS` on the downloaded source |
| CI secret/history scan | PASS | Included in the successful exact-commit CI workflow; checkout uses full history and read-only contents permission |
| Current GitHub identity and private permission | PASS | The authorized GitHub connection identified the intended account and verified read permission on a separate disposable Private repository; no private repository identity is published here |
| Private project read | PASS | The authorized connection read the private project's pinned entry, state, tests and tasks; exact repository and commit evidence remain in the restricted Agent task record, not this public repository |
| Auth/error-path fixtures | PASS_WITH_FIXTURES | 34 bootstrap tests include 401, wrong account, no integration, 403, ambiguous private 404, authoritative missing, offline, denial, and saved-intent resume for READ/SYNC/BACKUP; no real authorization was revoked |
| Independent new Luna Max resume | PASS | A fresh Luna Max used only the public Skill repository and a separate disposable Private repository, installed both Skills from the pinned release into a clean temporary destination, verified 24/24 private repository files against its tree and answered all 20 Resume questions with private repository evidence; exact identifiers are intentionally not published |
| Historical tagged-doc consistency | FAIL_IN_V1_0_0 | The immutable `v1.0.0` source README and `VERSION.json` said publication/CI were pending, despite the live formal Release and passing CI. Preserve that historical tag; version `1.0.1` removes transient status from `VERSION.json` and directs Agents to current GitHub Release/CI. |
| Version 1.0.1 release / final continuity sync / exact CI / readback | PENDING | This report, static version record and project brain must be committed, pushed without force, checked against final remote HEAD, then formally tagged and installed from the new tag |
| First ordinary disaster backup / restore | PENDING | Pack the final verified source; upload as distinct continuity prerelease asset, download and verify it, restore to a new directory, then verify context and tests |

The downloaded tagged ZIP is a GitHub-generated source archive, not a backup or product deployment. `github-bootstrap` preserves intent and reclassifies fresh evidence after official authorization, but cannot launch an OAuth flow in an environment that offers no such integration. A browser login alone does not grant the Agent GitHub API access. No unrelated project or deployment system was modified in this task.
