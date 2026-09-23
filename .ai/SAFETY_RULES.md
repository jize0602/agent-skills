# Safety Rules

- Keep this public project generic. Do not add private project facts, customer data, credentials, tokens, or authenticated browser state.
- Never request or expose credentials in chat. Use only an authorized official GitHub connector or CLI.
- Browser sign-in does not substitute for GitHub connector/CLI authorization. If that connection is unavailable, the user performs the official authorization themselves. Preserve the pending task intent and resume after authorization.
- `auto_sync` authorizes only the scoped continuity updates described by the project contract. It does not authorize deployment, releases, or unrelated remote changes.
- Do not record tests, manifest validation, secret scanning, publication, or remote state as complete unless current evidence supports that exact claim.
- CI uses read-only repository permissions and must not deploy or access external secrets.
