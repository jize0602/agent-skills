# Requirements

Status: public `v1.0.1` release and tagged-source clean install passed; final project-brain sync and first backup pending.

| ID | Requirement | Current evidence |
| --- | --- | --- |
| R1 | Publish reusable `github-bootstrap` and existing generic `github-continuity` from Public `jize0602/agent-skills` | `v1.0.1` formal Release ID 394930627 / exact-commit CI PASS; historical `v1.0.0` retained |
| R2 | Discover, install and validate pinned official release with complete SHA-256 manifest | Anonymous tagged README and GitHub tag ZIP read; clean install and hash/version verification PASS |
| R3 | Detect GitHub integration, identity and private permission; classify 401/403/404/offline/wrong account safely | State-machine tests pass; actual authorized private read succeeded; auth-loss paths simulated |
| R4 | Preserve the original operation through official authorization and resume automatically | State-machine tests pass; real permission revocation intentionally not performed |
| R5 | Make this repository continuity-aware with scoped task-end sync, CI and remote readback | Schema-1 project brain valid; initial public source CI/readback PASS; final acceptance sync pending |
| R6 | Public Release, anonymous read, new Luna Max resume, backup and recovery evidence | 1.0.1 Release/tagged-source install PASS; independent 20/20 Resume passed on the earlier release; first backup pending |

Never publish private source, runtime secrets, credentials, or a second conflicting project brain. The three user commands remain `从github <项目名>读取`, `同步至github <项目名>`, and `备份至github <项目名>`.

Implementation tracking pointer: `.ai/IMPLEMENTATION.md` ([open](IMPLEMENTATION.md))
