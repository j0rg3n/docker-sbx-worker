# docker-sbx-worker

Runs Claude Code unattended on GitHub issues inside a [Docker Sandbox](https://docs.docker.com/ai/sandboxes/)
(`sbx`) microVM that has **no credentials, no writable host files and no network**, apart from:

- the Anthropic API (added automatically by sbx's `claude` kit),
- a **forge broker** on the host that performs a fixed set of GitHub operations on one repository,
- the **package proxies** the project needs (npm, PyPI, NuGet), and only those.

```
┌────────────── sbx microVM (policy: deny-all, --clone mode) ──────────────┐
│ Claude Code + skills: issue-worker, feature-work   ~/.sbx/project.md     │
│ private clone of the repo  ── origin ──▶ /run/sandbox/source (read-only) │
└──┬──────────────────────┬───────────────────────────▲────────────────────┘
   │ host.docker.internal │ host.docker.internal      │ git-daemon remote
   │ :8765 (per project)  │ :4873 / :5000 / :5555     │ (host fetches from VM)
┌──▼──────────────────────┴───┐  ┌────────────────────┴────────────────────┐
│ pull-through caches         │  │ forge broker (MCP), holds the GitHub    │
│ verdaccio / proxpi /        │  │ token. Owns the "source" clone that sbx │
│ bagetter, 127.0.0.1 only    │  │ mounts read-only; fetches agent         │
└──┬──────────────────────────┘  │ branches over the git-daemon remote,    │
   ▼ public registries           │ checks them, pushes to GitHub.          │
                                 └──┬──────────────────────────────────────┘
                                    ▼ api.github.com / github.com
```

## What the sandbox can do on GitHub

| Tool | Limits enforced by the broker |
|---|---|
| `list_issues`, `get_issue` | This repo only; optionally only issues with `required_label` |
| `comment_issue` | Same issues; optional footer appended |
| `set_issue_status` | Only labels in `status_labels`; swaps one for another |
| `list_pull_requests` | Open PRs, first 500 characters of each body |
| `create_pull_request` | Branch must start with `branch_prefix`; fast-forward only, so no force push; rejected if it touches `forbidden_paths` (default `.github/`); opens a draft PR by default |
| `sync_base` | Refreshes the read-only source from GitHub; the agent then runs `git fetch origin` |

The agent never pushes and never holds a credential. When it calls `create_pull_request`, the
broker fetches that branch from the sandbox's git daemon into its own clone, checks the diff,
and pushes it to GitHub. Only the broker's own repository runs git on the host.

**Secret scanner.** Anything the broker publishes can be read by whoever can read the repo,
so it refuses text that looks like a credential. It checks comments, PR titles and bodies,
branch names, commit messages and the lines a PR adds:

- **Known formats** are refused everywhere: Anthropic, GitHub, AWS, Slack and Google keys,
  JWTs and private keys.
- **The broker's own GitHub token and client token** are refused everywhere, matched exactly.
- **Long random-looking strings** are refused only in prose (comments, PR text, commit
  messages, branch names). Code diffs are exempt, because lockfile hashes would trigger it.

Binary files in a PR are not scanned.

## Verified on sbx v0.45.1

A throwaway sandbox confirmed each of these:

- In `--clone` mode, the host repo is mounted read-only at `/run/sandbox/source`, and writes to it fail.
- The agent's commits can be fetched on the host through the `sandbox-<name>` remote.
- New commits in the host source reach the VM with `git fetch origin`.
- Under the default policy, `host.docker.internal:<port>` returns 403. It returns 200 after
  `sbx policy allow network localhost:<port> --sandbox <name>`, and other ports stay blocked.
- `api.github.com` is blocked.
- Skills and config can be copied into the VM through `sbx exec -i` stdin. With `--skills off`,
  sbx's shared skills store stays out, so your other sandboxes are unaffected.
- The base image (`claude-code-docker`, Ubuntu 26.04) already has git, node, npm, python3, pip and uv.
- `template/Dockerfile` builds with the host Docker Engine; `dotnet-sdk-10.0` is in the 26.04 repos.

Still to confirm on the first real run:

- Passing the broker config inline with `sbx run --name N -- --mcp-config '<json>'`.
- `sbx template load` taking the image from the `docker save` tar that `template/build.sh` writes.

## Setup

1. **Install sbx** with the `docker-sbx-wsl2` skill, then run `sbx login`.

2. **SSH agent forwarding.** sbx forwards your host SSH agent into sandboxes by default
   (`SSH_AUTH_SOCK` is set inside the VM). `sbx-worker up` switches it off and offers to
   restart the sbx daemon. The restart also restarts your other sandboxes.

3. **Create a fine-grained GitHub token** for the one repository. Grant *Contents*, *Issues*
   and *Pull requests* read/write; *Metadata* read is added automatically. Do **not** grant
   *Workflows*, *Administration* or *Secrets*.

   ```bash
   mkdir -p ~/.config/sbx-worker/{projects,tokens}
   install -m 600 /dev/stdin ~/.config/sbx-worker/tokens/layeredlight   # paste, Enter, Ctrl-D
   cp projects/layeredlight.toml ~/.config/sbx-worker/projects/
   ```

4. **Create the status labels** in the repo. For LayeredLight they are `ready`, `in-progress`,
   `in-review` and `needs-refinement`.

5. **Run it.**

   ```bash
   bin/sbx-worker up layeredlight
   # inside Claude:  /loop 30m /issue-worker
   # detach with Ctrl-b d; the loop keeps running
   bin/sbx-worker attach layeredlight   # come back later
   bin/sbx-worker down layeredlight     # stop the loop and the broker
   ```

   Claude runs in the tmux session `sbxw-<project>`, so closing the terminal doesn't stop the
   loop. If that session is running, `up` only attaches, so the broker isn't restarted under
   a running loop. Don't use a plain `sbx run --name sbxw-<project>`: it starts a second
   Claude that has no broker connection.

   Remote control (`/remote-control`) needs the global sbx setting `claude.remoteControl`,
   which puts a session token inside the VM. The loop doesn't need remote control; use
   `attach` instead.

## Per-project config

Real configs live in `~/.config/sbx-worker/projects/<name>.toml`. See `projects/example.toml`
for the full set of options. The main ones:

- `packages = ["npm"]` chooses which proxies to start and allow for this sandbox: any of
  `npm`, `pypi` and `nuget`. Package managers are pointed at them through environment
  variables baked in at creation, plus `~/.nuget/NuGet/NuGet.Config`.
- `[github]` sets the repo, base branch and token file.
- `[broker]` sets the port (one per project), branch prefix, status labels, forbidden paths
  and draft PRs.
- `[agent]` sets the working directory, install, test and verify commands, design docs,
  notes, label roles and an optional `template`. These are written to `~/.sbx/project.md`,
  which the skills read, so the skills themselves contain nothing project-specific.

Changing `packages` or `template` only takes effect on a new sandbox, so run
`bin/sbx-worker rm <project>` and then `up`.

## Security notes

- **Issue text is untrusted.** Anyone who can comment on an issue can attempt prompt
  injection. The most they can achieve is what the broker allows: comments, status labels
  and PRs on this one repo. A human still reviews and merges. `required_label` narrows
  which issues the agent can see at all.
- **CI runs the agent's code with the repo's secrets.** A branch pushed to the same repo
  triggers `push` and `pull_request` workflows. The broker refuses changes to `.github/`,
  and the token cannot change workflows, but an existing workflow still runs the agent's
  modified test code and could leak secrets. Put secrets behind environments that need
  approval, or don't run secret-bearing jobs for `agent/*` branches.
- **Package proxies are a narrow outbound channel.** A requested package name reaches the
  public registry, so a determined agent could leak a few bytes per request. The proxies
  accept no publishes: Verdaccio allows no users; BaGetter's API key is random, stored in
  `~/.config/sbx-worker/proxies.env`, and never given to the sandbox; proxpi has no upload
  feature.
- **claude.ai connectors are blocked.** Your account's connectors (mail, Jira, Slack, ...)
  reach the VM through `mcp-proxy.anthropic.com`, which the claude kit allows. Each sandbox
  gets a deny rule for that host, and deny beats allow. Claude also starts with
  `ENABLE_CLAUDEAI_MCP_SERVERS=false` and `--strict-mcp-config`, so connectors don't appear
  in `/mcp`. The deny rule is what counts, because the agent could start another `claude`
  process without those settings.
- **sbx's MCP gateway must stay empty.** sbx gives every VM an MCP gateway at
  `mcp-gateway.docker.internal`, and the network policy does not apply to it (a deny rule was
  tested and had no effect). Its tools `mcp-find` and `mcp-add` offer every server registered
  on the host with `sbx mcp add`, and `--command` servers run on the host itself. So
  `sbx-worker up` refuses to start while any server is registered. It also removes the
  gateway from the VM's `~/.claude.json`, and Claude runs with `--strict-mcp-config`.
- **sbx's own credential features stay unused.** The launcher sets `GH_TOKEN` to empty in
  the sandbox. Check `sbx secret ls` for any global `github` secret: its proxy would inject
  that secret into requests to github.com if a policy ever allowed that host.
- The broker's bearer token only unlocks that project's broker, which that sandbox can
  already reach. It stops other sandboxes and local processes from using the broker.

## Open issues

- **Inherited "ask before installing" rules can stall the loop.** Instructions from the
  account or organization, such as a rule to get the user's OK before installing outside
  software, also reach the Claude inside the sandbox. The unattended loop then stops before
  `npm ci` and waits for a yes that never comes. For now this is left as an extra safety
  check. A possible fix is a per-project standing approval in the host config
  (`agent.approve_package_installs`). It would be written into `~/.sbx/project.md` and cover
  only installs through the project's package proxies.
- **The loop's cron job expires.** A recurring job made with `/loop` stops after 7 days, so
  run `/loop` again after that.

## Tests

```bash
python3 broker/test_broker.py    # stub GitHub API + git daemon standing in for the VM, 33 checks
```
