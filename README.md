# Agent Skills

Install github-continuity from jize0602/agent-skills.

From a new Agent, say: `从 jize0602 的公共 Skill 仓库安装 github-continuity，然后从github <项目名>读取`. The Agent should fetch a pinned official release, validate it, and install both `github-bootstrap` and `github-continuity` using its supported Skill mechanism. If that mechanism is unavailable, it must say so rather than claim installation.

```text
从github <项目名>读取
同步至github <项目名>
备份至github <项目名>
```

> **Release check:** this repository's source files are snapshots, not live publication status. Select the [latest formal GitHub Release](https://github.com/jize0602/agent-skills/releases), resolve its tag to a commit, and require that commit's successful CI before installing. `VERSION.json` records Skill versions only; [`ACCEPTANCE.md`](ACCEPTANCE.md) records dated evidence, while current GitHub Releases and Actions decide current status.

## Skills

- [`github-bootstrap`](skills/github-bootstrap/SKILL.md) handles GitHub authorization state, preserves pending READ/SYNC/BACKUP intent, and provides manifest-verified local installation.
- [`github-continuity`](skills/github-continuity/SKILL.md) provides repository continuity, synchronization, backup, and recovery workflows.
- `.ai/skills/agent-skills-project/SKILL.md` is an internal project-maintenance skill; it is not one of the two public release skills.

The official release source and `SKILL_MANIFEST.json` must first be pinned to the same tag/commit in `jize0602/agent-skills`. The installer at `skills/github-bootstrap/scripts/install.py` checks the exact file set, versions and SHA-256 values of that downloaded source before copying either Skill; it does not execute their contents. A manifest supplied by an unknown fork does not prove provenance. See the [official Skills documentation](https://developers.openai.com/plugins/concepts/skills) for folder format.

## GitHub authorization

Browser sign-in does not by itself authorize an agent's GitHub connector or Git CLI. If the required official connection is unavailable, the user completes the official authorization flow personally. Keep the requested operation pending and continue it after the authorized connection is available; do not ask for or handle tokens in chat.

## Bootstrap and checks

`github-bootstrap` is the GitHub authorization and intent-preservation skill. The `context.py` script under `github-continuity` is a separate thin local project-brain bootstrap: it initializes and validates `.ai/CONTINUITY.json`; it does not log in, connect to GitHub, synchronize, or back up a repository.

Read-only CI runs `github-bootstrap` tests, `github-continuity` tests, release-manifest tests, schema-1 continuity validation, generated-manifest and SHA-256 verification, and a full-history secret scan. Its checkout uses `fetch-depth: 0` and repository permission is `contents: read`.

The project brain starts at [`.ai/START_HERE.md`](.ai/START_HERE.md). Version and evidence state are recorded in [`VERSION.json`](VERSION.json) and the mapped files under `.ai/`.
