# GitHub publication review

Remote: `origin` → `https://github.com/VedantSheel08/flowstate.git`.
The remote was empty and authenticated account `VedantSheel08` had push/admin
permission when inspected on 2026-10-17. No push, merge, deployment, tag publication,
branch protection change or history rewrite has been performed.

Existing GitHub CLI credentials are already configured as Git's HTTPS credential
helper. Authentication requires no action now. If they expire, run
`gh auth login --hostname github.com --git-protocol https --web`,
then `gh auth setup-git` and `gh auth status`. Never put a token in the remote URL.

## Branch policy

| Branch | Inspected commit | Publication status |
|---|---|---|
| main | 0de23f6 | Stable historical baseline, unchanged |
| task/frontend | 68a9dba | Completed committed snapshot only; newer working changes excluded |
| task/backend-performance | 6432bd3 | Completed experimental backend work, not congestion-certified |
| task/signals | 0b49c08 | Completed experimental snapshot only; newer working changes excluded |
| task/scenarios | 0de23f6 | Hold: unfinished working changes, no scenario commit yet |
| task/congestion-coordination | e2e5d08 | Completed audit/report/acceptance tools; preserve as its own branch |
| task/repository-setup | see publication manifest | Isolated cleanup/configuration/docs; no feature integration |

Frontend and signals are actively edited; their approved publication must use the
recorded commit IDs, not a moving branch name. Recheck the remote before publication.
Do not use `--all`, `--mirror`, `--force`, `--tags`, reset, squash or rebase.
No upstream tracking exists yet; set it after successful approved publication if
needed. Publishing snapshots is not approval to merge or certify the simulator.

The initial commit really is dated October 16 at 21:53:19 -04:00. Later commits
begin at 22:31:15. Preserve their original author, committer and timestamps exactly;
do not fabricate a history starting after 22:00. Existing author email addresses
will be public with the commits. The repository itself is public.

## Security and generated files

The setup branch expands ignore rules for local environment files, credentials,
virtual environments, Vercel local metadata and TypeScript cache. It removes 12
reproducible `_district`/`_district_bike` netconvert intermediates from its own tip.
Final SUMO networks, sidecars and bus-stop definitions remain versioned because the
baseline serves those files. Existing historical intermediates remain in original
commits; removing them from all history would violate the history-preservation goal.
Other agents' worktrees and main are unchanged. Ignore rules do not untrack files
already committed and do not propagate to other branches until deliberately adopted.

`tools/repository/publication_audit.py` inventories exact pinned refs, all reachable
commits with original metadata, per-commit changed files, and every complete branch
file tree including blob IDs and sizes. It scans reachable historical blobs for
credential signatures and suspicious filenames, without printing matched values.
This heuristic scan is not a guarantee that arbitrary secrets cannot exist.
Runtime logs, credentials, virtual environments, node_modules, dist output and
uncommitted agent changes must stay out of publication.

Run the audit after committing setup work:

```powershell
python tools/repository/publication_audit.py
```

Review the local ignored `data/work/publish-audit/PUBLICATION_MANIFEST.md` and
`SECURITY_AUDIT.json`. The manifest contains exact SHA-pinned individual push
commands. Obtain the user's approval of that manifest before any push. If snapshots
change, regenerate it and obtain approval for the changed publication set.

## Integration hold

The coordinator's nine sustained-peak runs recorded 42 native collision records
and 20 discarded vehicles; no congestion preset passed the proposed gates.
The global pending-departure cap is 10. These remain release blockers.
Do not merge the experimental branches into main or claim combined validation.
Keep coordination report and acceptance tests on `task/congestion-coordination`.

See [deployment preparation](DEPLOYMENT.md) for frontend configuration and the
unapplied owner handoff patch. No running Claude terminal was interrupted.
