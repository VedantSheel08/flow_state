# Repository setup verification — 2026-10-17

All frontend checks ran in disposable exports of committed Git trees beneath this
worktree's ignored `data/work/publish-audit`, using Node 22.14.0 and npm 10.9.2.
No other agent's source, dependency folder or build output was modified.

| Snapshot | Installation | Production build | Existing tests |
|---|---|---|---|
| main `0de23f6` | `npm ci` passed | Passed, 2549 transformed modules | 5 passed, 1 file |
| frontend `68a9dba` | `npm ci` passed | Passed, 2557 transformed modules | 18 passed, 3 files |
| frontend `68a9dba` plus proposed URL patch | Same clean lockfile install | Passed, 2557 transformed modules | 18 passed, 3 files |

The patch passed `git apply --check` against the disposable frontend snapshot.
The patched build used `VITE_API_BASE_URL=https://api.example.org` and
`VITE_WS_URL=wss://api.example.org/ws`; both configured values were verified in
the generated JavaScript. These are example addresses, not a live backend test.
The patch remains unapplied to every published branch's frontend source.

Vite/Vitest warn that their current configs use `__dirname`, which a future default
native config loader may not support. The current builds/tests pass. This is an
owner handoff, not a reason to change the active frontend worktree here.

`frontend/vercel.json` parses successfully. Ignore checks confirm `.env`,
`.env.local`, `frontend/.env`, `frontend/.env.production`, local `.vercel` metadata
and generated `_district` scratch files are ignored on the setup branch;
`.env.example` remains allowed. Final networks are retained.

`python -m py_compile tools/repository/publication_audit.py` passed. The audit
reads original reachable commits and writes an exact local publication manifest
plus a redacted historical blob scan. See `SECURITY_AUDIT.json` in the local audit
directory for final scan counts; it is a heuristic credential scan.

`gh auth status` identifies the active account as `VedantSheel08` with `repo`
scope. Authenticated repository API reports `push: true` and `admin: true`.
Git's HTTPS credential helper delegates to GitHub CLI. Read-only `git ls-remote`
confirms no remote refs at inspection. No push (including dry-run), deployment,
merge, history rewrite or remote settings mutation has been performed.

The baseline, backend-performance and congestion-coordination worktrees were clean
when inspected. Frontend, signals and scenarios have owner working changes, all
excluded from the proposed publication. Main still points to `0de23f6`.
