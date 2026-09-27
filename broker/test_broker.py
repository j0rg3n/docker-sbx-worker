#!/usr/bin/env python3
"""End-to-end test of forge_broker against a stub GitHub API and a local bare repo.

  python3 broker/test_broker.py
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
BROKER_PORT = 18765
STUB_PORT = 18766
DAEMON_PORT = 19418

ISSUES = {
    1: {"number": 1, "title": "Fix the thing", "state": "open", "body": "Please fix",
        "labels": [{"name": "agent"}, {"name": "status/todo"}]},
    2: {"number": 2, "title": "Not for the agent", "state": "open", "body": "secret",
        "labels": []},
}
PULLS = []
CALLS = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _issue(self, n):
        i = dict(ISSUES[n])
        i.update(html_url=f"https://example/issues/{n}", updated_at="now", user={"login": "alice"})
        return i

    def handle_any(self):
        assert self.headers["Authorization"] == "Bearer ghp_test", "token not sent"
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        path = self.path.split("?")[0].removeprefix("/repos/o/r")
        CALLS.append((self.command, self.path, body))
        parts = path.strip("/").split("/")
        if parts == ["issues"]:
            return self._json(200, [self._issue(k) for k in ISSUES])
        if parts[0] == "issues" and len(parts) == 2:
            return self._json(200, self._issue(int(parts[1])))
        if parts[0] == "issues" and parts[2] == "comments":
            if self.command == "POST":
                return self._json(201, {"html_url": "https://example/c/1"})
            return self._json(200, [])
        if parts[0] == "issues" and parts[2] == "labels":
            labels = ISSUES[int(parts[1])]["labels"]
            if self.command == "POST":
                labels.extend({"name": x} for x in body["labels"])
            else:
                labels[:] = [l for l in labels if l["name"] != urllib.request.unquote(parts[3])]
            return self._json(200, labels)
        if parts == ["pulls"]:
            if self.command == "POST":
                PULLS.append(body)
                return self._json(201, {"html_url": "https://example/pull/1"})
            return self._json(200, [{"number": 1, "title": p["title"], "head": {"ref": p["head"]},
                                     "html_url": "https://example/pull/1", "body": p["body"]} for p in PULLS])
        self._json(404, {"message": "stub: " + path})

    do_GET = do_POST = do_DELETE = handle_any


def sh(*args, cwd):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


def mcp(method, params=None, token="client-secret", _id=[0]):
    _id[0] += 1
    req = urllib.request.Request(f"http://127.0.0.1:{BROKER_PORT}/mcp", method="POST",
                                 data=json.dumps({"jsonrpc": "2.0", "id": _id[0], "method": method,
                                                  "params": params or {}}).encode(),
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def call(name, **args):
    res = mcp("tools/call", {"name": name, "arguments": args})["result"]
    return res["isError"], res["content"][0]["text"]


def main():
    tmp = Path(tempfile.mkdtemp())
    origin = tmp / "origin.git"
    up = tmp / "upstream"            # someone else pushing to GitHub
    ws = tmp / "vm" / "repo"         # the agent's private clone inside the VM
    source = tmp / "state" / "source"
    sh("git", "init", "-q", "--bare", "-b", "main", str(origin), cwd=tmp)
    sh("git", "clone", "-q", str(origin), str(up), cwd=tmp)
    for k, v in (("user.email", "a@b"), ("user.name", "t")):
        sh("git", "config", k, v, cwd=up)
    (up / "README").write_text("hi\n")
    sh("git", "add", ".", cwd=up); sh("git", "commit", "-qm", "init", cwd=up)
    sh("git", "push", "-q", "origin", "main", cwd=up)

    (tmp / "token").write_text("ghp_test\n")
    cfg = tmp / "p.toml"
    cfg.write_text(f"""
name = "test"
[github]
repo = "o/r"
token_file = "{tmp / 'token'}"
api_url = "http://127.0.0.1:{STUB_PORT}"
git_url = "{origin}"
[broker]
listen_port = {BROKER_PORT}
state_dir = "{tmp / 'state'}"
status_labels = ["status/todo", "status/in-progress", "status/review"]
required_label = "agent"
""")
    stub = ThreadingHTTPServer(("127.0.0.1", STUB_PORT), Stub)
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    broker = subprocess.Popen([sys.executable, str(HERE / "forge_broker.py"), "--config", str(cfg)],
                              env={**os.environ, "SBX_BROKER_CLIENT_TOKEN": "client-secret"})
    try:
        for _ in range(50):
            try:
                mcp("ping"); break
            except OSError:
                time.sleep(0.1)

        failures = []
        def check(label, cond):
            print(("PASS " if cond else "FAIL ") + label)
            if not cond:
                failures.append(label)

        try:
            mcp("ping", token="wrong")
            check("wrong client token rejected", False)
        except urllib.error.HTTPError as e:
            check("wrong client token rejected", e.code == 401)

        init = mcp("initialize", {"protocolVersion": "2025-06-18"})["result"]
        check("initialize", init["protocolVersion"] == "2025-06-18")
        names = [t["name"] for t in mcp("tools/list")["result"]["tools"]]
        check("seven tools listed", len(names) == 7)

        err, out = call("list_issues")
        check("list_issues filters by required label", not err and "Not for the agent" not in out)
        check("list_issues sent required label", any("labels=agent" in c[1] for c in CALLS))
        err, out = call("get_issue", number=2)
        check("get_issue refuses unlabeled issue", err)
        err, out = call("get_issue", number=1)
        check("get_issue returns body", not err and "Please fix" in out)
        err, out = call("comment_issue", number=1, body="On it")
        check("comment_issue", not err and "example/c/1" in out)
        err, out = call("set_issue_status", number=1, status="closed-by-agent")
        check("set_issue_status refuses unknown label", err)
        err, out = call("set_issue_status", number=1, status="status/in-progress")
        labels = [l["name"] for l in ISSUES[1]["labels"]]
        check("set_issue_status swaps label", not err and labels == ["agent", "status/in-progress"])

        # What `sbx-worker init` + `sbx create --clone` do: broker builds the source,
        # the VM clones it (origin = read-only source), sbx serves the VM clone over git-daemon.
        err, out = call("sync_base")
        check("sync_base creates the source clone", not err and (source / ".git").exists())
        ws.parent.mkdir(parents=True)
        sh("git", "clone", "-q", str(source), str(ws), cwd=tmp)
        for k, v in (("user.email", "a@b"), ("user.name", "t")):
            sh("git", "config", k, v, cwd=ws)
        daemon = subprocess.Popen(["git", "daemon", "--export-all", "--reuseaddr", "--listen=127.0.0.1",
                                   f"--port={DAEMON_PORT}", f"--base-path={ws.parent}", str(ws.parent)])
        time.sleep(0.5)
        sh("git", "remote", "add", "sandbox-sbxw-test", f"git://127.0.0.1:{DAEMON_PORT}/repo", cwd=source)
        try:
            sh("git", "checkout", "-qb", "agent/fix-1", "origin/main", cwd=ws)
            (ws / "fix.txt").write_text("fixed\n")
            sh("git", "add", ".", cwd=ws); sh("git", "commit", "-qm", "fix", cwd=ws)

            err, out = call("create_pull_request", branch="main", title="t")
            check("PR refuses branch outside prefix", err)
            err, out = call("create_pull_request", branch="agent/../main", title="t")
            check("PR refuses '..' in branch", err)
            err, out = call("create_pull_request", branch="agent/nope", title="t")
            check("PR fails cleanly for a missing branch", err and "ghp_test" not in out)

            err, out = call("create_pull_request", branch="agent/fix-1", title="Fix 1", body="b")
            check("PR created", not err and "created" in out)
            pushed = subprocess.run(["git", "rev-parse", "refs/heads/agent/fix-1"], cwd=origin,
                                    capture_output=True, text=True).stdout.strip()
            local = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ws, capture_output=True, text=True).stdout.strip()
            check("branch pushed to origin", pushed == local)
            check("no incoming ref left behind", "incoming" not in subprocess.run(
                ["git", "for-each-ref"], cwd=source, capture_output=True, text=True).stdout)

            (ws / "fix.txt").write_text("fixed more\n")
            sh("git", "commit", "-qam", "more", cwd=ws)
            err, out = call("create_pull_request", branch="agent/fix-1", title="Fix 1")
            check("PR updated by fast-forward", not err and "updated" in out)

            sh("git", "commit", "-q", "--amend", "-m", "rewritten", cwd=ws)
            err, out = call("create_pull_request", branch="agent/fix-1", title="Fix 1")
            check("force push rejected", err)

            sh("git", "checkout", "-qb", "agent/ci", "origin/main", cwd=ws)
            (ws / ".github" / "workflows").mkdir(parents=True)
            (ws / ".github" / "workflows" / "x.yml").write_text("on: push\n")
            sh("git", "add", ".", cwd=ws); sh("git", "commit", "-qm", "ci", cwd=ws)
            err, out = call("create_pull_request", branch="agent/ci", title="ci")
            check("changes under .github/ rejected", err and ".github/workflows/x.yml" in out)

            sh("git", "checkout", "-qb", "agent/mv", "origin/main", cwd=ws)
            sh("git", "checkout", "-q", "agent/ci", "--", ".github", cwd=ws)
            sh("git", "commit", "-qm", "add", cwd=ws)
            err, out = call("create_pull_request", branch="agent/mv", title="mv")
            check("still rejected when combined with other changes", err)

            # Secret scanner on everything the broker publishes
            fake = "sk-ant-api03-" + "Zx9Qw8Er7Ty6Ui5Op4As3Df2"
            err, out = call("comment_issue", number=1, body=f"here it is: {fake}")
            check("comment with API key refused", err and fake not in out)
            err, out = call("comment_issue", number=1, body="value 8fK2mQ9xLp4R7tVbN3cW6yZ1aE5hJ0gU")
            check("comment with random token refused", err)
            err, out = call("comment_issue", number=1, body="ghp_test")
            check("comment with the broker's own GitHub token refused", err)
            err, out = call("comment_issue", number=1, body="Fixed in 9d79d90ee4c5d297fb3d36b75384e8cea7a4fbcb")
            check("comment with a commit SHA allowed", not err)
            err, out = call("create_pull_request", branch="agent/fix-1", title="t", body=f"k={fake}")
            check("PR body with API key refused", err)

            sh("git", "checkout", "-qb", "agent/leak-file", "origin/main", cwd=ws)
            (ws / "notes.txt").write_text(f"key: {fake}\n")
            sh("git", "add", ".", cwd=ws); sh("git", "commit", "-qm", "notes", cwd=ws)
            err, out = call("create_pull_request", branch="agent/leak-file", title="notes")
            check("PR adding a file with an API key refused", err and "changed files" in out)
            check("refused branch was not pushed", "agent/leak-file" not in subprocess.run(
                ["git", "branch", "-a"], cwd=origin, capture_output=True, text=True).stdout)

            sh("git", "checkout", "-qb", "agent/leak-msg", "origin/main", cwd=ws)
            (ws / "ok.txt").write_text("ok\n"); sh("git", "add", ".", cwd=ws)
            sh("git", "commit", "-qm", f"done {fake}", cwd=ws)
            err, out = call("create_pull_request", branch="agent/leak-msg", title="ok")
            check("PR with API key in commit message refused", err and "commit messages" in out)

            sh("git", "checkout", "-qb", "agent/lock", "origin/main", cwd=ws)
            (ws / "package-lock.json").write_text(
                '{"integrity": "sha512-Xk3mQ9pL2vR8sT4wY6zA1bC5dE7fG0hJ2kL4mN6oP8qR0sT2uV4wX6yZ8aB0cD2e=="}\n')
            sh("git", "add", ".", cwd=ws); sh("git", "commit", "-qm", "lock", cwd=ws)
            err, out = call("create_pull_request", branch="agent/lock", title="lockfile")
            check("PR with lockfile hashes allowed", not err)

            err, out = call("list_pull_requests")
            check("list_pull_requests shows agent branch", not err and "agent/fix-1" in out)

            # New upstream commit: sync_base puts it in the source; the VM fetches origin.
            sh("git", "pull", "-q", "--ff-only", cwd=up)
            (up / "new.txt").write_text("n\n"); sh("git", "add", ".", cwd=up)
            sh("git", "commit", "-qm", "upstream", cwd=up); sh("git", "push", "-q", "origin", "main", cwd=up)
            uptip = subprocess.run(["git", "rev-parse", "HEAD"], cwd=up, capture_output=True, text=True).stdout.strip()
            err, out = call("sync_base")
            check("sync_base reports the new commit", not err and uptip in out)
            sh("git", "fetch", "-q", "origin", cwd=ws)
            newtip = subprocess.run(["git", "rev-parse", "origin/main"], cwd=ws,
                                    capture_output=True, text=True).stdout.strip()
            check("VM sees it after git fetch origin", newtip == uptip)
        finally:
            daemon.terminate()

        print(f"\n{len(failures)} failure(s)")
        return 1 if failures else 0
    finally:
        broker.terminate()
        stub.shutdown()


if __name__ == "__main__":
    sys.exit(main())
