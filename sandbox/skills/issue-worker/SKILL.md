---
name: issue-worker
description: One iteration of the unattended issue loop inside the sandbox - sync the base branch through the forge broker, pick the oldest open issue with the ready status that has no open PR, and hand it to the feature-work skill on a fresh branch. Use from `/loop` (e.g. `/loop 30m /issue-worker`).
---

# Issue worker (one iteration, sandboxed)

Handles **at most one issue** per run, then stops. Read `~/.sbx/project.md` for `<base>`,
`<prefix>`, the status labels, `<work_dir>` and the install command. GitHub access is only
through the **forge** MCP tools; there is no `gh` and no network.

## 1. Preconditions

- `git status --porcelain` must be empty. If it isn't, stop and report; don't clean up
  someone else's work.
- The forge tools must respond: call `list_pull_requests`. If it fails, stop and report.

## 2. Sync

Call `sync_base`, then:

```bash
git fetch origin --prune
git checkout --detach origin/<base>
```

Run the install command in `<work_dir>` if the lock file changed since the last run or the
dependency folder is missing.

## 3. Pick an issue

Call `list_issues` with `status` set to the ready label, and use the `list_pull_requests`
result from step 1.

Skip issues that already have an open PR (a branch `<prefix>issue-<n>-*`, or `#<n>` in the
body start). Pick the oldest remaining one (lowest number).

If none remain, report "no ready issues" and stop. That's a normal outcome.

## 4. Work it

```bash
git checkout -b <prefix>issue-<n>-<short-slug> origin/<base>
```

Then invoke the `feature-work` skill with the issue number. It ends by either opening a PR
or asking for refinement on the issue.

## 5. Reset

Whatever the outcome:

```bash
git checkout --detach origin/<base>
```

Delete the local issue branch. Report one line: `#<n> → PR <url>`, `#<n> → needs-refinement`,
or `no ready issues`.
