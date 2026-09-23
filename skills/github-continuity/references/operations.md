# Initialization, backup, recovery and acceptance

## Unified project contract

`.ai/CONTINUITY.json` has schema_version=1, repository=owner/repo, branch, auto_sync,
files mapping and test_commands. For an initialized project, `auto_sync` defaults to
`true`: it is the standing authorization for the formal development-task completion
push hook, not a background service. A read-only or no-push request overrides that
hook for the current task and must not trigger a push. File roles: entry, project, requirements,
implementation, history, decisions, incidents, lessons, tasks, tests, deployment,
safety, handoff, project_skill, changelog. Paths stay within repository.
Map existing equivalents before initialization; one role may point to an existing
combined document. Never generate competing sources. START_HERE links reading order.
Requirements track stable IDs, acceptance, progress and implementation/test pointers.
Incidents record date, cause, impact, recovery, fix and prevention/tests; lessons
record demonstrated pitfalls and effective/ineffective methods, not speculation.

Acceptance status is strict: `PASS` requires every required remote, CI and readback
gate to succeed. Incomplete or unavailable evidence is `PARTIAL` or `UNKNOWN`;
permission, conflict, failed-test and failed-CI gates are `BLOCKED`. None of
`PARTIAL`, `BLOCKED`, `UNKNOWN`, `NOT_RUN`, `PENDING` or `STALE` may be reported
as `PASS`.

## Initialize

Resolve identity and repository using existing authorization. If the project name or
full `owner/repo` is absent, use only a verified unique repository mapping in the
current project contract or Git remote; otherwise ask for the name. Never infer it
from the working-directory name, old chat, or an ambiguous same-name search. Distinguish
confirmed missing from unavailable: a 403/404 alone is not proof of absence. First
verify the authenticated identity and its repository visibility/read permission;
only a confirmed missing repository may lead to one request to create a Private
repository. A permission failure is `BLOCKED`; an offline or unverified GitHub
connection is `UNKNOWN`/`PARTIAL`; neither may trigger creation or push. New
repository creation is Private by default, and Public requires separate explicit
confirmation. Initialize an isolated local checkout, preserve existing files,
context.py init then fill real project facts (unknowns stay UNKNOWN), project Skill
and scoped AGENTS completion rule.
Add ignore rules and minimal read-only CI running real checks, no deploy, secrets or
paid calls. Scan all proposed content and reachable history. Commit reviewed paths,
push normally or use Git tree/commit/ref connector preserving parent and full tree.
Verify actual visibility (Private by default; Public only after separate explicit
user confirmation), exact remote tree/commit, CI and context readback. A Public receipt
must set `explicit_public_authorization=true`. Perform independent Resume Test then
first verified disaster backup. Missing stages stay PARTIAL.

## Sync and concurrency

For an initialized project, the `auto_sync=true` hook runs only at formal
development-task completion and only when the task is not read-only/no-push; it is
not a scheduler or background monitor. Fetch immediately before reconciliation and
capture base/remote HEAD. If remote is ahead, inspect changes; rebase/merge only
scoped non-conflicting work and rerun affected checks. Preserve dirty local files in
a separate checkout. Never force-push or overwrite newer remote state. Connector
`update_ref` uses `force=false` and a commit whose parent is the verified current
HEAD; reject non-fast-forward races.

After any write, fetch remote HEAD/tree again before producing the receipt. If the
remote advances after base capture, during the write, or before CI/readback, stop
the current acceptance as `BLOCKED`, preserve both sides, and re-reconcile from the
new remote base. Rerun the affected CI for the reconciled exact commit and repeat
remote commit/tree and key-file readback. Never reset, roll back, force-push or
overwrite the newer remote state, and never reuse the pre-race receipt. Same-content
web commits do not prove local commit ancestry was pushed. No empty commit to test
sync.

CI must belong to the reconciled exact commit and every required workflow. Zero runs,
queued, skipped, cancelled, failure, wrong SHA or missing workflow is `BLOCKED`;
pending or unavailable evidence is `PARTIAL`/`UNKNOWN`; none is PASS. Only completed
successful runs plus exact remote commit/tree and required readback can produce PASS.
Inspect any push deployment triggers before writing.
Latest commit is reported in external receipt to avoid self-referential SHA commits.

## Secret handling

Scan working tree, proposed commit and all outgoing reachable history, including
deleted blobs. Never print secret values. Unsupported binary/nested archives require
explicit inspection, never a silent exemption. Pattern scanners are partial tools;
inspect actual staged diff and data provenance too. Secret found: stop ordinary push,
record only redacted path/category, assess exposure and credential rotation. Prepare
history repair in an isolated copy; shared history rewriting/force push requires
explicit approval. Never automatically rotate/revoke credentials or erase history.

Ordinary static image/font files may use a committed `.ai/STATIC_ASSET_REVIEWS.json`
JSON array, each record containing exactly `path`, `sha256`, `reviewer`, `reason`.
The reviewer must actually inspect the asset and its provenance before recording a
review; the scanner verifies exact path, bytes, basic format and embedded textual
secret patterns, not the reviewer's identity or the entire file format. Keep a
separate review record for every distinct reachable historical hash at that path;
duplicate path/hash records are invalid. Valid
reviewed assets report `PASS_WITH_REVIEW`; absent/malformed review, stale hash,
unknown binary, runtime data or unsupported archive remains `BLOCKED`/`REJECTED`.
Do not create a blanket extension allowlist or treat `PASS_WITH_REVIEW` as proof
that unexamined media contains no private data.

## Backup storage and verification

Use clean verified remote commit. Include tracked source, all mapped context, docs,
safe config templates, restore instructions, metadata and exact SHA256 manifest.
Snapshot is a source/context disaster recovery artifact, not a production database
backup; runtime user data and secrets are excluded. Metadata records full commit/tree,
repository, UTC created_at, schema_version, kind=ordinary-continuity-backup and
explicit false flags protected/production/formal/milestone. Dedicated tag format:
continuity-backup-YYYYMMDD-HHMMSS. Never reuse a tag or overwrite an asset.

Publish as non-latest prerelease with archive and SHA256 checksum; authenticate via
existing connector or gh. gh must already be authenticated; never extract browser
tokens. Download asset from actual Release, compare local and remote SHA256, verify
manifest and repository/commit against GitHub, restore to nonexistent directory,
validate all mapped context and run appropriate tests. Keep verification receipt
outside the snapshot, with release/asset IDs, hashes, commit and test evidence.

Only after new backup verifies, fetch complete paginated release inventory. Read
metadata, protections and asset evidence; ambiguous records are protected. Retention
planner returns exact IDs only for verified ordinary backups beyond newest three.
Main Agent reviews each planned deletion and refetches to prevent stale decisions.
Delete only those ordinary release assets/releases; preserve tags, Git history,
product releases, formal releases, production releases, milestones and user-locked
snapshots. A formal release is protected even when it is not also marked production
or milestone.
If permissions/tool policy requires user action, report exact blocked deletion;
never claim retention success while four ordinary snapshots remain.

## Restore

GitHub current state is authoritative when reachable. Offline archive is explicitly
dated stale snapshot. Verify checksum from independent stored/upload receipt, archive
manifest, metadata and path safety before extraction. Restore into new destination,
never over existing work or server data. Recovery may reconstruct working source and
context without .git; reconnect/fetch GitHub history separately when available. Do
not push a historic restored snapshot over newer repository content.

## Independent resume acceptance

New agent receives only repository identifier, read authority, and these questions.
No prior chat, local handoff, expected answers or summary. Cite remote files/commit:
1 project identity; 2 overall goal; 3 version; 4 current stage; 5 implemented;
6 missing; 7 current requirements; 8 implementation progress; 9 recent changes;
10 architecture reasons; 11 incidents; 12 errors not to repeat; 13 known pitfalls;
14 safety; 15 tested; 16 untested; 17 deployment; 18 blockers; 19 next step;
20 important source/context files. UNKNOWN is honest but unresolved required facts
keep acceptance PARTIAL. Run after init, major mapping changes and final recovery.

## Evidence statuses

Local fixtures prove only local behavior. Real E2E requires private repo creation,
initial push/CI/read, independent resume, change/sync/CI/read, four uploaded verified
backups with oldest ordinary removed, formal release/history preservation, restore
and final independent resume. Record exact URLs/SHAs for each. Test permission loss,
missing name/repo, secret, failed CI, concurrent remote update and offline snapshot
paths. Never label a simulated API test as real remote acceptance.
