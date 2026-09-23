# Test State

Overall status: `V1_0_0_BASELINE_AND_INDEPENDENT_RESUME_PASS_V1_0_1_RELEASE_BACKUP_PENDING` as of 2026-09-23. See [`ACCEPTANCE.md`](../ACCEPTANCE.md) for exact remote evidence. Current source version requires its own CI.

| Check | Command | Status |
| --- | --- | --- |
| Existing continuity unit tests | `python3 -m unittest discover -s skills/github-continuity/tests -q` | 61/61 PASS locally |
| Bootstrap state/install tests | `python3 -m unittest discover -s skills/github-bootstrap/tests -v` | 34/34 PASS locally |
| Release-manifest tests | `python3 -m unittest discover -s tests -v` | 6/6 PASS locally |
| Continuity contract | `python3 skills/github-continuity/scripts/context.py validate --root .` | PASS locally |
| Current-tree secret scan | `python3 skills/github-continuity/scripts/security.py --root .` | PASS locally |
| Full-history scan and public CI | GitHub [run 35863797817](https://github.com/jize0602/agent-skills/actions/runs/35863797817) at `8332b38d5f6d3fcf114bfe8e42069f37a738533b` | PASS |
| Official tag archive / clean install / 101 tests | `v1.0.0` GitHub ZIP, pinned tag and manifest | PASS |
| Anonymous tagged README | signed-out Chrome Incognito | PASS |
| Independent GitHub-only private-project resume | new Luna Max, 24/24 pinned remote files and 20/20 questions; private identifiers withheld | PASS |
| Current source release/CI and first backup | separate acceptance gates | PENDING |

The `v1.0.0` tagged snapshot retained stale candidate wording even though GitHub Release/CI passed; this is being corrected in source version `1.0.1` without moving the historical tag. CI and local/tagged-archive evidence are tracked separately; neither proves a newer release or backup.
