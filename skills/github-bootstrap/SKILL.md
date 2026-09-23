---
name: github-bootstrap
description: Discover and install the official jize0602 github-continuity Skill, validate a pinned release, check GitHub identity and private-repository access, and resume the original READ, SYNC or BACKUP request after official authorization.
---

# GitHub bootstrap

This is a thin entry layer. It does not reimplement `github-continuity` or grant GitHub access. Use it when an Agent must install/update the public Skill, or when a continuity request needs identity, repository-permission, and authorization checks. The published source is exactly `https://github.com/jize0602/agent-skills`; an unknown fork is not an installation source.

## Discover, install, validate

1. Check whether a compatible installed `github-continuity` already exists. Do not force an upgrade on every request. For a new environment, use its trusted, built-in Skill installer if available to fetch **only** `jize0602/agent-skills` at an explicit formal Release tag. If no Skill-install capability exists, report `SKILL_INSTALL_UNSUPPORTED`; do not pretend the folder is active. In Codex, a Skill installed during a running task may require a fresh Agent session before automatic invocation, so continue the current request using the read instructions from the verified folder without claiming live auto-discovery.
2. Pin the official repository identity and resolve the release tag to its commit through GitHub. Obtain the release source bundle and `SKILL_MANIFEST.json` from that same official tag; do not use a floating `main` archive as a release. Verify that the manifest names the official repository and expected tag, then check the complete file set, SHA-256 values, and versions before treating the install as valid. `scripts/install.py` validates a previously downloaded bundle; its manifest is an integrity check, not independent proof of publisher identity. Do not execute a file fetched from an unverified fork.
3. Keep compatible installed versions. For an explicit update, verify the new release before replacing anything; preserve the existing working version until the replacement is validated. A breaking continuity contract needs a new major version.

## Check GitHub and continue

1. Save the original `intent` (`READ`, `SYNC` or `BACKUP`, owner, repository and optional branch) before any authorization step. Check the current platform's GitHub integration, authenticated identity, target repository metadata/visibility/permissions and a pinned file read. Browser login alone does not establish connector or CLI access.
2. Classify the evidence with `scripts/auth.py`. An inaccessible private repository is never automatically created: 403 is forbidden; 404 and an App-scoped repository listing are ambiguous. Only independent authoritative absence evidence permits asking whether to create a **Private** repository. Public creation needs separate explicit user authority.
3. If authorization is needed and this platform exposes an official GitHub OAuth/App/connector connection action, invoke that action and ask the user only to complete GitHub's own login/permission confirmation. The user, not this Skill, chooses any `All repositories` scope. Never request a password, PAT, token, cookie or private key in chat. If this environment offers no official connection action, report the exact missing capability and retain the intent; do not fabricate an OAuth URL or use browser cookies.
4. After the user completes authorization, recheck identity and the *same* target repository using fresh evidence. Pass the pending result and fresh observation to `resume`. On `AUTHORIZED`, continue the saved operation through `github-continuity` without asking the user to repeat it. A denial ends this attempt without repeated prompts. Offline state may use a dated local snapshot only as an explicitly stale snapshot, never as current GitHub state.

This skill uses `scripts/auth.py` as a standard-library-only, side-effect-free state machine. It classifies supplied evidence; it does not contact GitHub, sign in, create repositories, read browser cookies, or handle credentials.

## Contract

Pass an `intent` object with `operation` set to `READ`, `SYNC` or `BACKUP`, plus `owner`, `repository` and optional `branch`. Pass an `observation` object with exactly these fields:

`integration`, `online`, `identity`, `expected_owner`, `repo_http`, `repo_access`, `owner_repository_listing_complete`, `owner_repository_present`, `repo_absence_authoritative`, and `auth_denied`.

`evaluate(intent, observation)` returns a status, the unchanged intent, `next_action`, `can_continue`, `resumable`, and `may_request_private_creation`. Only `AUTHORIZED` sets `can_continue=true`; the caller may then continue the operation named by the preserved intent. A pending result can be passed to `resume(state, fresh_observation)`. Resume reclassifies fresh evidence without changing the operation, owner, repository or branch. A refused authorization is terminal for that attempt and cannot be resumed.

## Decision rules

- `auth_denied=true` means the user refused authorization: return `AUTH_REQUIRED` with `next_action=stop`; do not ask again in the same attempt.
- Authorization, account selection, or connector setup must happen only through the official platform GitHub connector. Direct the user to that connector when needed; never ask for a token, inspect cookies, or claim that this skill can launch an unavailable OAuth flow.
- `integration=false` returns `NO_GITHUB_INTEGRATION`; `online=false` returns `OFFLINE`.
- HTTP 401 or a missing identity returns `AUTH_REQUIRED`. An identity different from `expected_owner` returns `WRONG_ACCOUNT`.
- A known identity different from `expected_owner` returns `WRONG_ACCOUNT`, including when the repository request also returned 403 or 401. It remains non-authorized and never permits repository creation. HTTP 403 without a known wrong identity returns `FORBIDDEN` and stops.
- HTTP 404 by itself, including with `repo_access=false`, returns `UNKNOWN_REPOSITORY_ACCESS`. It does not establish that a repository is absent.
- A complete paginated owner listing can still be scoped to repositories visible to a GitHub App, so its absence is not authoritative. HTTP 404 and listing absence alone return `UNKNOWN_REPOSITORY_ACCESS`.
- `REPO_NOT_FOUND` requires HTTP 404, identity authenticated as `expected_owner`, `expected_owner` equal to the intent's owner, and `repo_absence_authoritative=true`. That flag means an independent check had explicit full-repository visibility or management authority and confirmed absence. Any positive access or repository-presence evidence makes the result unknown. HTTP 401, 403, 429, 500, or no HTTP result cannot be classified as missing from this flag. This state may request explicit user confirmation for a private repository; it never creates one.
- Only verified repository access returns `AUTHORIZED`. Incomplete, contradictory, or otherwise insufficient evidence returns `UNKNOWN_REPOSITORY_ACCESS`.
- `UNSUPPORTED_ENVIRONMENT` is available through `unsupported_environment(intent)` when the caller cannot supply the contract in the current execution environment. It preserves the intent for a later supported environment.

The state machine never performs `READ`, `SYNC`, `BACKUP`, OAuth, or repository creation itself. A caller must honor `next_action` and only continue a requested operation after `AUTHORIZED`.
