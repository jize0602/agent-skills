# Requirements

Status: implementation candidate; remote release and independent acceptance pending.

| ID | Requirement | Current evidence |
| --- | --- | --- |
| R1 | Publish reusable `github-bootstrap` and existing generic `github-continuity` 1.0.0 from Public `jize0602/agent-skills` | Public repository ID 1383044358 exists; Skill release pending |
| R2 | Discover, install and validate pinned official release with complete SHA-256 manifest | Scripts and local negative tests pass; remote clean install pending |
| R3 | Detect GitHub integration, identity and private permission; classify 401/403/404/offline/wrong account safely | State-machine tests pass; actual authorized private read succeeded; auth-loss paths simulated |
| R4 | Preserve the original operation through official authorization and resume automatically | State-machine tests pass; real permission revocation intentionally not performed |
| R5 | Make this repository continuity-aware with scoped task-end sync, CI and remote readback | Schema-1 project brain valid locally; new public CI/readback pending |
| R6 | Public Release, anonymous read, new Luna Max resume, backup and recovery evidence | Pending end-to-end verification; do not report PASS early |

Never publish private source, runtime secrets, credentials, or a second conflicting project brain. The three user commands remain `从github <项目名>读取`, `同步至github <项目名>`, and `备份至github <项目名>`.

Implementation tracking pointer: `.ai/IMPLEMENTATION.md` ([open](IMPLEMENTATION.md))
