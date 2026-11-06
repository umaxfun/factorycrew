"""Forge adapters: GitLab (REST v4) and GitHub (REST + one GraphQL query). Standard library only.

Both implement `fcore.Forge`. HTTP goes through a `Transport`, so the tests drive the real
adapters on recorded responses. `make_forge` builds one from a web URL as people write it
(https://gitlab.example, https://github.com, https://ghe.example).
"""

from __future__ import annotations

import datetime
import json
import urllib.error
import urllib.parse
import urllib.request

from fcore import (  # bundle: drop
    MR,  # bundle: drop
    Check,  # bundle: drop
    Comment,  # bundle: drop
    Forge,  # bundle: drop
    ForgeError,  # bundle: drop
    Issue,  # bundle: drop
    fixes_issues,  # bundle: drop
    last_anchors,  # bundle: drop
)  # bundle: drop

PER_PAGE = 100



def _ts(value):
    """An ISO-8601 forge timestamp as an aware datetime, or None."""
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None

class Transport:
    """One HTTP request. Returns (status, body bytes). Raises nothing for HTTP errors."""

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() or b""


class _Http:
    def __init__(self, api: str, headers: dict, transport=None):
        self.api = api.rstrip("/")
        self.headers = headers
        self.transport = transport or Transport()

    def call(self, method: str, path: str, data=None, ok404: bool = False, raw: bool = False):
        url = path if path.startswith("http") else self.api + path
        body = json.dumps(data).encode() if data is not None else None
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        status, out = self.transport(method, url, headers, body)
        if status == 404 and ok404:
            return None
        if status >= 400:
            raise ForgeError(status, out[:300].decode("utf-8", "replace"))
        if raw:
            return out
        return json.loads(out) if out.strip() else None

    def pages(self, path: str, key: str | None = None) -> list:
        """Every page of a list. A failed page raises: a rule never decides on a partial list."""
        out, page = [], 1
        sep = "&" if "?" in path else "?"
        while True:
            rows = self.call("GET", f"{path}{sep}per_page={PER_PAGE}&page={page}")
            if key is not None:
                rows = (rows or {}).get(key) or []
            out.extend(rows or [])
            if len(rows or []) < PER_PAGE:
                return out
            page += 1


def q(s: str) -> str:
    return urllib.parse.quote(str(s), safe="")


# ---------------------------------------------------------------- GitLab

_GL_PENDING = {"created", "waiting_for_resource", "preparing", "pending", "running", "scheduled"}
_GL_STATUS = {"success": "success", "failed": "failed", "canceled": "cancelled", "skipped": "skipped",
              "manual": "manual"}
_GL_CHECKING = {"checking", "unchecked", "preparing", "approvals_syncing"}


class GitLab(Forge):
    kind = "gitlab"
    mr_ref = "!"

    def __init__(self, api: str, project: str, token: str, transport=None, token_header="PRIVATE-TOKEN"):
        self.http = _Http(api, {token_header: token}, transport)
        self.pid = q(project)
        self.p = f"/projects/{self.pid}"
        self._target_ids: dict[int, int] = {}  # merge request -> its target project id

    def default_branch(self) -> str:
        return self.http.call("GET", self.p)["default_branch"]

    def _own_pipelines(self, mr):
        """The merge request's pipelines that ran in the target project, newest first.

        GitLab lists a fork's own pipelines under the merge request too; those run the fork's
        CI configuration (any job names, no gate path in the diff), so they never count.
        """
        target = self._target_ids.get(mr.number)
        if target is None:
            self.get_mr(mr.number)
            target = self._target_ids.get(mr.number)
        pipes = self.http.call("GET", f"{self.p}/merge_requests/{mr.number}/pipelines") or []
        return [p for p in pipes if target is not None and p.get("project_id") == target]

    def read_file(self, path, ref):
        out = self.http.call("GET", f"{self.p}/repository/files/{q(path)}/raw?ref={q(ref)}",
                             ok404=True, raw=True)
        return None if out is None else out.decode("utf-8")

    def blob_id(self, path, ref):
        out = self.http.call("GET", f"{self.p}/repository/files/{q(path)}?ref={q(ref)}", ok404=True)
        return None if out is None else out["blob_id"]

    def branch_exists(self, branch):
        return self.http.call("GET", f"{self.p}/repository/branches/{q(branch)}", ok404=True) is not None

    def _mr(self, m: dict) -> MR:
        status = m.get("detailed_merge_status") or m.get("merge_status") or ""
        if status in _GL_CHECKING:
            mergeable = "checking"
        elif status == "conflict" or m.get("has_conflicts"):
            mergeable = "conflict"
        elif status == "need_rebase":
            mergeable = "behind"
        else:
            mergeable = "ok"
        if m.get("target_project_id") is not None:
            self._target_ids[m["iid"]] = m["target_project_id"]
        return MR(number=m["iid"], title=m.get("title") or "", body=m.get("description") or "",
                  source=m["source_branch"], target=m["target_branch"], head=m["sha"],
                  draft=bool(m.get("draft") or m.get("work_in_progress")),
                  labels=tuple(m.get("labels") or ()), author=(m.get("author") or {}).get("username", ""),
                  mergeable=mergeable,
                  same_repo=m.get("source_project_id", 0) == m.get("target_project_id", 0))

    def open_mrs(self):
        rows = self.http.pages(f"{self.p}/merge_requests?state=opened&order_by=created_at&sort=asc"
                               "&with_merge_status_recheck=true")
        return [self._mr(m) for m in rows]

    def get_mr(self, number):
        return self._mr(self.http.call("GET", f"{self.p}/merge_requests/{number}"))

    def checks(self, mr):
        pipes = self._own_pipelines(mr)
        if not pipes or pipes[0]["sha"] != mr.head:
            return None
        p = pipes[0]
        jobs = self.http.pages(f"{self.p}/pipelines/{p['id']}/jobs?include_retried=false")
        out = [Check(name=j["name"], status="pending" if j["status"] in _GL_PENDING
                     else _GL_STATUS.get(j["status"], j["status"]),
                     allow_failure=bool(j.get("allow_failure")), id=int(j.get("id") or 0),
                     started=j.get("created_at") or p.get("created_at") or "")
               for j in jobs]
        if p.get("status") in _GL_PENDING:
            out.append(Check(name=f"pipeline {p['id']}", status="pending"))
        return out

    def blocked_review_heads(self, mr, jobs, limit=20):
        # Earlier MR pipelines, newest first (the first page is enough for `limit` heads).
        out, seen = set(), set()
        for p in self._own_pipelines(mr):
            sha = p.get("sha") or ""
            if not sha or sha == mr.head or sha in seen:
                continue
            if len(seen) >= limit:
                break
            seen.add(sha)
            for j in self.http.pages(f"{self.p}/pipelines/{p['id']}/jobs?include_retried=false"):
                if j.get("name") in jobs and j.get("status") == "failed" and not j.get("allow_failure"):
                    out.add(sha)
                    break
        return out

    def changed_paths(self, mr):
        diffs = self.http.pages(f"{self.p}/merge_requests/{mr.number}/diffs")
        return sorted({d["new_path"] for d in diffs} | {d["old_path"] for d in diffs})

    def open_threads(self, mr):
        ds = self.http.pages(f"{self.p}/merge_requests/{mr.number}/discussions")
        return sum(1 for d in ds if any(n.get("resolvable") and not n.get("resolved")
                                        for n in d.get("notes") or []))

    def comments(self, mr):
        notes = self.http.pages(f"{self.p}/merge_requests/{mr.number}/notes?sort=asc&order_by=created_at")
        out = [Comment((n.get("author") or {}).get("username", ""), n.get("body") or "",
                       n.get("created_at") or "") for n in notes if not n.get("system")]
        return sorted(out, key=lambda c: c.created_at)  # oldest first, as on GitHub

    def approvers(self, mr):
        # An approval counts for the head only when it was given after that head was pushed.
        # GitLab keeps approvals across pushes unless the project resets them ("Remove all
        # approvals when a commit is added", Premium), so the approval API alone could carry an
        # approval of an older head. The head's push time is its first merge request diff
        # version; an approval's time is the latest "approved this merge request" system note
        # of that user. No version for the head (not read, or not there yet): nobody approved it.
        a = self.http.call("GET", f"{self.p}/merge_requests/{mr.number}/approvals") or {}
        current = {x["user"]["username"] for x in a.get("approved_by") or []}
        if not current:
            return set()
        pushed = [_ts(v.get("created_at")) for v in self.http.pages(f"{self.p}/merge_requests/{mr.number}/versions")
                  if v.get("head_commit_sha") == mr.head]
        pushed = [t for t in pushed if t is not None]
        if not pushed:
            return set()
        since = min(pushed)
        approved_at: dict = {}
        for n in self.http.pages(f"{self.p}/merge_requests/{mr.number}/notes"):
            if n.get("system") and (n.get("body") or "").strip() == "approved this merge request":
                who, t = (n.get("author") or {}).get("username", ""), _ts(n.get("created_at"))
                if t is not None and (who not in approved_at or t > approved_at[who]):
                    approved_at[who] = t
        return {u for u in current if u in approved_at and approved_at[u] >= since}

    def closing_issues(self, mr):
        # GitLab links `Fixes #N` only for MRs into the default branch; for a feature branch the
        # same lines are read from the body.
        rows = self.http.pages(f"{self.p}/merge_requests/{mr.number}/closes_issues")
        return sorted({int(i["iid"]) for i in rows}) or fixes_issues(mr.body)

    def label_events(self, issue):
        rows = self.http.pages(f"{self.p}/issues/{issue}/resource_label_events")
        return [{"created_at": e.get("created_at") or "", "action": e.get("action"),
                 "label": (e.get("label") or {}).get("name") or "",
                 "user": (e.get("user") or {}).get("username", "")} for e in rows]

    def mr_label_events(self, mr):
        rows = self.http.pages(f"{self.p}/merge_requests/{mr.number}/resource_label_events")
        return [{"created_at": e.get("created_at") or "", "action": e.get("action"),
                 "label": (e.get("label") or {}).get("name") or "",
                 "user": (e.get("user") or {}).get("username", "")} for e in rows]

    def issue_is_open(self, issue):
        return (self.http.call("GET", f"{self.p}/issues/{issue}", ok404=True) or {}).get("state") == "opened"

    def issue(self, number):
        # A `Fixes #N` line can name an issue that does not exist here (copied from upstream):
        # it is no issue, so it is not open and carries no label.
        i = self.http.call("GET", f"{self.p}/issues/{number}", ok404=True) or {}
        return Issue(number=int(number), title=i.get("title") or "", body=i.get("description") or "",
                     author=(i.get("author") or {}).get("username", ""), open=i.get("state") == "opened",
                     labels=tuple(i.get("labels") or ()))

    def issue_comments(self, issue):
        notes = self.http.pages(f"{self.p}/issues/{issue}/notes?sort=asc&order_by=created_at")
        out = [Comment((n.get("author") or {}).get("username", ""), n.get("body") or "",
                       n.get("created_at") or "") for n in notes if not n.get("system")]
        return sorted(out, key=lambda c: c.created_at)

    def merge(self, mr):
        self.http.call("PUT", f"{self.p}/merge_requests/{mr.number}/merge",
                       {"sha": mr.head, "should_remove_source_branch": mr.same_repo})

    def rebase(self, mr):
        self.http.call("PUT", f"{self.p}/merge_requests/{mr.number}/rebase")

    def rebase_in_progress(self, mr):
        m = self.http.call("GET", f"{self.p}/merge_requests/{mr.number}?include_rebase_in_progress=true")
        return bool((m or {}).get("rebase_in_progress"))

    def refresh(self, mr):
        m = self.http.call("GET", f"{self.p}/merge_requests/{mr.number}?include_diverged_commits_count=true")
        if mr.mergeable == "behind" or int((m or {}).get("diverged_commits_count") or 0) > 0:
            self.rebase(mr)
            return "rebased"
        self.http.call("POST", f"{self.p}/merge_requests/{mr.number}/pipelines")
        return "new pipeline"

    def comment(self, mr, body):
        self.http.call("POST", f"{self.p}/merge_requests/{mr.number}/notes", {"body": body})

    def comment_issue(self, issue, body):
        self.http.call("POST", f"{self.p}/issues/{issue}/notes", {"body": body})

    def close_issue(self, issue):
        self.http.call("PUT", f"{self.p}/issues/{issue}", {"state_event": "close"})

    def remove_issue_label(self, issue, label):
        self.http.call("PUT", f"{self.p}/issues/{issue}", {"remove_labels": label})

    def branch_status(self, branch):
        # The latest FINISHED pipeline: while a new one runs, the last result still stands.
        rows = self.http.call("GET", f"{self.p}/pipelines?ref={q(branch)}&scope=finished"
                                     "&order_by=id&sort=desc&per_page=1") or []
        if not rows:
            return "unknown", [], ""
        p = rows[0]
        where = p.get("web_url") or f"pipeline {p['id']}"
        if p.get("status") == "success":
            return "green", [], where
        if p.get("status") != "failed":
            return "unknown", [], where
        jobs = self.http.pages(f"{self.p}/pipelines/{p['id']}/jobs?include_retried=false&scope[]=failed")
        return "red", sorted({j["name"] for j in jobs if j.get("status") == "failed"
                              and not j.get("allow_failure")}), where

    def find_issues(self, label):
        rows = self.http.pages(f"{self.p}/issues?state=opened&labels={q(label)}")
        return sorted(int(i["iid"]) for i in rows)

    def create_issue(self, title, body, labels):
        out = self.http.call("POST", f"{self.p}/issues", {"title": title, "description": body,
                                                        "labels": ",".join(labels)})
        return int(out["iid"])

    def update_issue(self, issue, body):
        self.http.call("PUT", f"{self.p}/issues/{issue}", {"description": body})

    def review_anchors(self, mr, check):
        # The job log; review.sh prints the anchors line after the transcript.
        out = self.http.call("GET", f"{self.p}/jobs/{check.id}/trace", ok404=True, raw=True)
        return None if out is None else last_anchors(out.decode("utf-8", "replace"), mr.head)


# ---------------------------------------------------------------- GitHub

_GH_DONE = {"success": "success", "failure": "failed", "timed_out": "failed", "startup_failure": "failed",
            "action_required": "failed", "cancelled": "cancelled", "skipped": "skipped", "stale": "skipped",
            "neutral": "neutral"}
_GH_STATE = {"success": "success", "failure": "failed", "error": "failed", "pending": "pending"}
STATUS_APP = "commit-status"

_THREADS = """query($o:String!,$r:String!,$n:Int!,$c:String){repository(owner:$o,name:$r){
pullRequest(number:$n){reviewThreads(first:100,after:$c){nodes{isResolved}
pageInfo{hasNextPage endCursor}}}}}"""


class GitHub(Forge):
    kind = "github"
    mr_ref = "#"

    def __init__(self, api: str, project: str, token: str, transport=None, graphql: str = "",
                 merge_method: str = "merge"):
        self.http = _Http(api, {"Authorization": f"Bearer {token}",
                                "Accept": "application/vnd.github+json",
                                "X-GitHub-Api-Version": "2022-11-28"}, transport)
        self.graphql = graphql or (api.rstrip("/").removesuffix("/v3") + "/graphql"
                                   if api.rstrip("/").endswith("/api/v3") else api.rstrip("/") + "/graphql")
        self.owner, self.repo = project.split("/", 1)
        self.r = f"/repos/{q(self.owner)}/{q(self.repo)}"
        self.merge_method = merge_method

    def default_branch(self):
        return self.http.call("GET", self.r)["default_branch"]

    def read_file(self, path, ref):
        path_q = "/".join(q(p) for p in path.split("/"))
        out = self.http.call("GET", f"{self.r}/contents/{path_q}?ref={q(ref)}", ok404=True)
        if out is None:
            return None
        import base64  # noqa: PLC0415
        return base64.b64decode(out.get("content") or "").decode("utf-8")

    def blob_id(self, path, ref):
        path_q = "/".join(q(p) for p in path.split("/"))
        out = self.http.call("GET", f"{self.r}/contents/{path_q}?ref={q(ref)}", ok404=True)
        if out is None:
            return None
        if not isinstance(out, dict) or out.get("type", "file") != "file":
            raise ForgeError(200, f"{path} at {ref} is not a file")
        return out["sha"]

    def branch_exists(self, branch):
        return self.http.call("GET", f"{self.r}/branches/{q(branch)}", ok404=True) is not None

    def _mr(self, p: dict) -> MR:
        state = p.get("mergeable_state") or "unknown"
        if p.get("mergeable") is None or state == "unknown":
            mergeable = "checking"
        elif state == "dirty" or p.get("mergeable") is False:
            mergeable = "conflict"
        elif state == "behind":
            mergeable = "behind"
        else:
            mergeable = "ok"
        head_repo = ((p.get("head") or {}).get("repo") or {}).get("full_name")
        base_repo = ((p.get("base") or {}).get("repo") or {}).get("full_name")
        return MR(number=p["number"], title=p.get("title") or "", body=p.get("body") or "",
                  source=p["head"]["ref"], target=p["base"]["ref"], head=p["head"]["sha"],
                  draft=bool(p.get("draft")), labels=tuple(x["name"] for x in p.get("labels") or []),
                  author=(p.get("user") or {}).get("login", ""), mergeable=mergeable,
                  same_repo=head_repo is not None and head_repo == base_repo)

    def open_mrs(self):
        rows = self.http.pages(f"{self.r}/pulls?state=open&sort=created&direction=asc")
        # The list has no mergeability: read each pull request.
        return [self.get_mr(p["number"]) for p in rows]

    def get_mr(self, number):
        return self._mr(self.http.call("GET", f"{self.r}/pulls/{number}"))

    def checks(self, mr):
        runs = self.http.pages(f"{self.r}/commits/{mr.head}/check-runs?filter=all", key="check_runs")
        wf_runs = self.http.pages(f"{self.r}/actions/runs?head_sha={mr.head}", key="workflow_runs")
        suite_path = {w.get("check_suite_id"): (w.get("path") or "").split("@", 1)[0] for w in wf_runs}
        out = []
        for c in runs:
            status = "pending" if c.get("status") != "completed" else _GH_DONE.get(c.get("conclusion"), "failed")
            out.append(Check(name=c["name"], status=status, app=((c.get("app") or {}).get("slug") or ""),
                             workflow=suite_path.get((c.get("check_suite") or {}).get("id"), ""),
                             id=int(c.get("id") or 0), started=c.get("started_at") or ""))
        combined = self.http.call("GET", f"{self.r}/commits/{mr.head}/status") or {}
        for s in combined.get("statuses") or []:
            out.append(Check(name=s["context"], status=_GH_STATE.get(s.get("state"), "failed"),
                             app=STATUS_APP, id=int(s.get("id") or 0), started=s.get("created_at") or ""))
        return out or None

    def blocked_review_heads(self, mr, jobs, limit=20):
        # The pull request's commits (a head rebased away is no longer listed), newest `limit`.
        shas = [c["sha"] for c in self.http.pages(f"{self.r}/pulls/{mr.number}/commits")
                if c.get("sha") and c["sha"] != mr.head][-limit:]
        out = set()
        for sha in shas:
            runs = self.http.pages(f"{self.r}/commits/{sha}/check-runs?filter=latest", key="check_runs")
            if any(r.get("name") in jobs and (r.get("app") or {}).get("slug") == "github-actions"
                   and r.get("conclusion") in ("failure", "timed_out") for r in runs):
                out.add(sha)
        return out

    def changed_paths(self, mr):
        files = self.http.pages(f"{self.r}/pulls/{mr.number}/files")
        # GitHub lists at most 3000 files: a shorter list than the pull request's own count
        # could hide a gate path, and a rule never decides on a partial list.
        total = (self.http.call("GET", f"{self.r}/pulls/{mr.number}") or {}).get("changed_files")
        if isinstance(total, int) and len(files) < total:
            raise ForgeError(200, f"pull request #{mr.number} lists {len(files)} of {total} changed files")
        return sorted({f["filename"] for f in files} | {f["previous_filename"] for f in files
                                                         if f.get("previous_filename")})

    def open_threads(self, mr):
        n, cursor = 0, None
        while True:
            out = self.http.call("POST", self.graphql, {"query": _THREADS, "variables": {
                "o": self.owner, "r": self.repo, "n": mr.number, "c": cursor}})
            if out.get("errors"):
                raise ForgeError(200, json.dumps(out["errors"])[:300])
            t = out["data"]["repository"]["pullRequest"]["reviewThreads"]
            n += sum(1 for x in t["nodes"] if not x["isResolved"])
            if not t["pageInfo"]["hasNextPage"]:
                return n
            cursor = t["pageInfo"]["endCursor"]

    def comments(self, mr):
        rows = self.http.pages(f"{self.r}/issues/{mr.number}/comments")
        return [Comment((c.get("user") or {}).get("login", ""), c.get("body") or "", c.get("created_at") or "")
                for c in rows]

    def approvers(self, mr):
        # The latest review of each person counts, and only when it approved the current head.
        last = {}
        for r in self.http.pages(f"{self.r}/pulls/{mr.number}/reviews"):
            if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                last[(r.get("user") or {}).get("login", "")] = r
        return {u for u, r in last.items() if r["state"] == "APPROVED" and r.get("commit_id") == mr.head}

    def closing_issues(self, mr):
        return fixes_issues(mr.body)

    def label_events(self, issue):
        rows = self.http.pages(f"{self.r}/issues/{issue}/timeline")
        return [{"created_at": e.get("created_at") or "",
                 "action": "add" if e["event"] == "labeled" else "remove",
                 "label": (e.get("label") or {}).get("name") or "",
                 "user": (e.get("actor") or {}).get("login", "")}
                for e in rows if e.get("event") in ("labeled", "unlabeled")]

    def mr_label_events(self, mr):
        return self.label_events(mr.number)  # a pull request's timeline is its issue's

    def issue_is_open(self, issue):
        return (self.http.call("GET", f"{self.r}/issues/{issue}", ok404=True) or {}).get("state") == "open"

    def issue(self, number):
        # A `Fixes #N` line can name an issue that does not exist here: no issue, no label.
        i = self.http.call("GET", f"{self.r}/issues/{number}", ok404=True) or {}
        return Issue(number=int(number), title=i.get("title") or "", body=i.get("body") or "",
                     author=(i.get("user") or {}).get("login", ""), open=i.get("state") == "open",
                     labels=tuple(x["name"] if isinstance(x, dict) else str(x) for x in i.get("labels") or ()))

    def issue_comments(self, issue):
        rows = self.http.pages(f"{self.r}/issues/{issue}/comments")
        return [Comment((c.get("user") or {}).get("login", ""), c.get("body") or "", c.get("created_at") or "")
                for c in rows]

    def merge(self, mr):
        self.http.call("PUT", f"{self.r}/pulls/{mr.number}/merge",
                       {"sha": mr.head, "merge_method": self.merge_method})
        if mr.same_repo:
            try:
                self.http.call("DELETE", f"{self.r}/git/refs/heads/{q(mr.source)}")
            except ForgeError:
                pass  # a protected or already deleted branch: the merge itself is done

    def rebase(self, mr):
        # GitHub's API updates a branch by merging the base into it (no rebase endpoint).
        self.http.call("PUT", f"{self.r}/pulls/{mr.number}/update-branch", {"expected_head_sha": mr.head})

    def rebase_in_progress(self, mr):
        return False  # update-branch is fire-and-forget; a new head shows when it is done

    def refresh(self, mr):
        # Behind its base: update the branch (a new head runs every workflow). Up to date: re-run
        # the head's workflow runs (the merger's token then needs `actions: write`).
        cmp = self.http.call("GET", f"{self.r}/compare/{q(mr.target)}...{mr.head}") or {}
        if mr.mergeable == "behind" or int(cmp.get("behind_by") or 0) > 0:
            self.rebase(mr)
            return "updated"
        runs = self.http.pages(f"{self.r}/actions/runs?head_sha={mr.head}", key="workflow_runs")
        for w in runs:
            self.http.call("POST", f"{self.r}/actions/runs/{w['id']}/rerun")
        return f"re-ran {len(runs)} workflow run(s)"

    def comment(self, mr, body):
        self.http.call("POST", f"{self.r}/issues/{mr.number}/comments", {"body": body})

    def comment_issue(self, issue, body):
        self.http.call("POST", f"{self.r}/issues/{issue}/comments", {"body": body})

    def close_issue(self, issue):
        self.http.call("PATCH", f"{self.r}/issues/{issue}", {"state": "closed", "state_reason": "completed"})

    def remove_issue_label(self, issue, label):
        self.http.call("DELETE", f"{self.r}/issues/{issue}/labels/{q(label)}", ok404=True)

    def branch_status(self, branch):
        sha = self.http.call("GET", f"{self.r}/commits/{q(branch)}")["sha"]
        runs = self.http.pages(f"{self.r}/commits/{sha}/check-runs?filter=latest", key="check_runs")
        combined = self.http.call("GET", f"{self.r}/commits/{sha}/status") or {}
        states = [("pending" if c.get("status") != "completed"
                   else _GH_DONE.get(c.get("conclusion"), "failed"), c["name"]) for c in runs]
        states += [(_GH_STATE.get(s.get("state"), "failed"), s["context"])
                   for s in combined.get("statuses") or []]
        failing = sorted({n for st, n in states if st == "failed"})
        if failing:
            return "red", failing, f"commit {sha[:12]}"
        if any(st == "pending" for st, _n in states):
            return "pending", [], f"commit {sha[:12]}"
        return ("green" if states else "unknown"), [], f"commit {sha[:12]}"

    def find_issues(self, label):
        rows = self.http.pages(f"{self.r}/issues?state=open&labels={q(label)}")
        return sorted(int(i["number"]) for i in rows if "pull_request" not in i)

    def create_issue(self, title, body, labels):
        return int(self.http.call("POST", f"{self.r}/issues",
                                  {"title": title, "body": body, "labels": labels})["number"])

    def update_issue(self, issue, body):
        self.http.call("PATCH", f"{self.r}/issues/{issue}", {"body": body})

    def review_anchors(self, mr, check):
        # The job's annotations (the job log is a redirect to storage, and the token must not
        # follow it). review.sh writes the line as a `factory-anchors` notice after the
        # transcript, which it prints with workflow commands stopped.
        if check.app != "github-actions":
            return None
        rows = self.http.pages(f"{self.r}/check-runs/{check.id}/annotations")
        text = "\n".join("[FACTORY-ANCHORS] " + (a.get("message") or "").strip() for a in rows
                         if a.get("title") == "factory-anchors")
        return last_anchors(text, mr.head)


# ---------------------------------------------------------------- construction


def github_api(url: str) -> str:
    """REST base for a GitHub web URL: github.com, GHE.com (data residency) or GitHub Enterprise Server."""
    u = urllib.parse.urlsplit(url.rstrip("/"))
    host = u.netloc.lower()
    if host in ("github.com", "www.github.com", "api.github.com"):
        return "https://api.github.com"
    if host.endswith(".ghe.com"):
        return f"https://api.{host.removeprefix('api.')}"
    return f"{u.scheme}://{u.netloc}{u.path}/api/v3"


def make_forge(kind: str, url: str, project: str, token: str, transport=None, merge_method="merge") -> Forge:
    if kind == "gitlab":
        return GitLab(url.rstrip("/") + "/api/v4", project, token, transport)
    if kind == "github":
        return GitHub(github_api(url), project, token, transport, merge_method=merge_method)
    raise ValueError(f"unknown forge {kind!r}: gitlab or github")
