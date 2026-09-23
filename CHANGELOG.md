# Changelog

## 1.0.1 release evidence (2026-09-24)

- Published formal `v1.0.1` Release at `4d35a73016679634b2e94e346c0b0cdd7b915800`; exact main and tag CI passed.
- Verified the downloaded official tag archive, manifest and clean installation of both Skills; 101 tests and source secret scan passed.
- Kept the historical `v1.0.0` tag unchanged. Final project-brain sync and first continuity backup are tracked separately in `ACCEPTANCE.md`.

## 1.0.1 — release-status consistency

- Removed transient publication and CI claims from `VERSION.json`; current status is verified from GitHub Releases and Actions.
- Updated the project brain and acceptance record after the first public `v1.0.0` release and independent 20-question recovery test.
- No change to the continuity protocol, authorization state machine, or business permissions.

## 1.0.0 — public release, 2026-09-23

- Added public project documentation, installation guidance, and the three GitHub continuity prompts.
- Added a schema-1 continuity project brain and scoped automatic task-sync intent.
- Added a read-only CI workflow for both Skill test suites, release-manifest tests, continuity validation, exact manifest comparison and full-history secret scanning.
- Published both Skills at tag `v1.0.0` / commit `8332b38d5f6d3fcf114bfe8e42069f37a738533b`; public CI run `35863797817` passed. The tag archive passed manifest-verified clean installation and 101 tests.
- Kept the existing continuity protocol; public-repository receipt support requires explicit public authorization.
