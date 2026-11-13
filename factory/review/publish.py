#!/usr/bin/env python3
"""Show one AI review on its pull request (GitHub) or merge request (GitLab).

Usage: publish.py --model <model> --dir <folder that holds .review/> [--summary <file>]

The CI job review-comment-<model slug> runs it after ai-review-<model slug>, on that job's
artifact. It posts ONE comment per review model and edits that comment on later runs: the
verdict, the findings by severity, and for a blocked review the `/factory override` line a
person can copy. With --summary it also writes the whole review there (GitHub's job summary).

The job that runs this holds a token that can comment and nothing else: no model key, and no
code of the merge request. Everything in the artifact is DATA an author can influence (the
review quotes the diff, and a folder named .review/ can be committed or left behind), so:

- the verdict is the review job's exit code, which review.sh records in `result` together
  with the commit; no `result`, one for another commit, or exit 3 (the review was not
  needed) publishes nothing;
- PASS is said only when the FORGE also reports the review job as succeeded, so a planted
  `result` cannot say it;
- every piece of the review's text goes into the comment as one inline code span on a line
  this script starts, so it cannot become a heading, a link, a mention, an image, a quick
  action, an HTML comment or an override line;
- the override line carries `<class>` and `<reason>` for the person to fill in, so the
  comment itself is never an override, whoever posts it;
- nothing from the review is printed to the job log (GitHub reads workflow commands there).

A run for a commit that is not the merge request's head publishes nothing, so a late job does
not put an old verdict over a newer one. That also covers a GitLab merged-results pipeline or
merge train, which runs on a merge commit: the factory does not support those (go-live.md), and
an override line with that commit would be ignored by the merger.

It reads from the environment what tier.py reads, with a token that may write: GITHUB_TOKEN,
GITHUB_REPOSITORY, FACTORY_PR_NUMBER, FACTORY_HEAD_SHA, FACTORY_REVIEW_RESULT (GitHub);
FACTORY_COMMENT_TOKEN, CI_API_V4_URL, CI_PROJECT_ID, CI_MERGE_REQUEST_IID, CI_COMMIT_SHA,
CI_PIPELINE_ID (GitLab).

Exit 0: posted, edited, or nothing to post (no review ran, no token, the token cannot write).
Exit 1: the forge refused or failed otherwise. Exit 2: wrong usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, ".."), os.path.join(HERE, "..", "factory")):  # vendored, package
    if os.path.isfile(os.path.join(p, "fcore.py")):
        sys.path.insert(0, os.path.abspath(p))
        break

from fcore import ForgeError, review_job  # noqa: E402
from forges import GitHub, GitLab  # noqa: E402

MAX_REVIEW = 2 * 1024 * 1024   # bytes of review.md read, from its end (the verdict is there)
MAX_LISTED = 20                # findings listed per severity; the rest are counted
MAX_FIELD = 200                # characters of one field of a finding
MAX_SUMMARY = 900_000          # bytes; GitHub takes 1 MiB of job summary per step
ANCHORS = {"security", "crash", "data-loss", "corruption", "guard-removal", "broken-contract",
           "design-one-way-door", "product-shape", "evidence-gap", "none"}
GITHUB_BOT = "github-actions[bot]"   # the author of what a workflow's own token posts
SHA = re.compile(r"[0-9a-f]{40}\Z")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}\Z")
RESULT = re.compile(r"(\d{1,3}) ([0-9a-f]{40})\n?\Z", re.ASCII)

PASS, BLOCK, INFRA, FAILED = "PASS", "BLOCK", "INFRA FAILURE", "FAILED"
SAYS = {
    PASS: "No blocking finding.",
    BLOCK: "The review blocks this change.",
    INFRA: "A part of the review did not run and nothing was found: retry the job.",
    FAILED: "The review did not finish with a verdict (see the job's log): not checked is not clean.",
}


def mark(job: str) -> str:
    """The first line of the comment: how a later run finds the comment to edit."""
    return f"<!-- factory-review {job} -->"


def inline(text, limit: int = MAX_FIELD) -> str:
    """Untrusted text as one inline code span: one line, no control or invisible characters,
    no backtick to end the span early, and none of the tokens another reader of comments looks
    for anywhere in a body (an image for the screenshot rule, an HTML comment marker)."""
    s = "".join(" " if c.isspace() else c for c in str(text)[:50 * limit]
                if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    s = " ".join(s.split()).replace("`", "'")
    s = s.replace("![", "! [").replace("<!--", "<! --").replace("-->", "-- >")
    s = re.sub(r"(?i)<(?=/?img)", "< ", s)
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return f"`{s}`" if s else "`-`"


def _read(root: str, path: str) -> str | None:
    """The end of a plain file inside `root`, never a link out of it."""
    if os.path.islink(path) or not os.path.isfile(path) or \
            not os.path.realpath(path).startswith(os.path.realpath(root) + os.sep):
        return None
    with open(path, "rb") as fh:
        fh.seek(max(0, os.path.getsize(path) - MAX_REVIEW))
        return fh.read(MAX_REVIEW).decode("utf-8", "replace")


def read_result(root: str, slug: str, head: str):
    """(exit code, review text) of the review of `head`, or None when there is nothing to
    publish: review.sh recorded no result, one for another commit, or exit 3 (not needed)."""
    base = os.path.join(root, ".review", slug)
    m = RESULT.match(_read(root, os.path.join(base, "result")) or "")
    if not m or m.group(2) != head or int(m.group(1)) == 3:
        return None
    return int(m.group(1)), _read(root, os.path.join(base, "review.md")) or ""


def verdict(code: int, text: str, job_ok: bool) -> str:
    """review.sh's own decision, read from its exit code; never from the review's words alone.
    `job_ok`: the forge reports the review job as succeeded. Without that there is no PASS."""
    if code == 0:
        return PASS if job_ok else FAILED
    if code == 4:
        return INFRA
    lines = [ln.rstrip("\r").strip() for ln in text.split("\n") if ln.strip()]
    return BLOCK if code == 1 and lines and lines[-1] == "VERDICT: BLOCK" else FAILED


def findings(text: str):
    """(blocking, advisory) from the review's last JSON block; None when it cannot be read."""
    # The last block only, found from the end: a pattern over the whole text is tried again
    # from every "```json" in it, minutes on a crafted review of a few hundred kilobytes.
    start = text.rfind("```json")
    line_end = text.find("\n", start)
    end = text.find("\n```", line_end)
    if start < 0 or line_end < 0 or end < 0 or text[start + len("```json"):line_end].strip():
        return None
    try:
        rows = json.loads(text[line_end + 1:end]).get("findings")
    except (ValueError, AttributeError, TypeError, RecursionError):
        return None
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        return None
    rows = [f for f in rows if isinstance(f, dict)]
    return ([f for f in rows if f.get("severity") == "blocking"],
            [f for f in rows if f.get("severity") != "blocking"])


def _finding(f: dict) -> str:
    line = f.get("line")
    where = str(f.get("file") or "?")[:MAX_FIELD] + (
        f":{line}" if isinstance(line, int) and not isinstance(line, bool) else "")
    out = f"- {inline(where)} {inline(f.get('headline') or f.get('consequence') or '')}"
    anchor = f.get("anchor")
    if f.get("severity") == "blocking":
        out += f" (anchor {inline(anchor if isinstance(anchor, str) and anchor in ANCHORS else 'unknown', 30)})"
    if f.get("fix"):
        out += f" Fix: {inline(f['fix'])}"
    return out


def _section(title: str, rows: list) -> list[str]:
    out = [f"**{title} ({len(rows)})**", ""]
    out += [_finding(f) for f in rows[:MAX_LISTED]]
    if len(rows) > MAX_LISTED:
        out.append(f"- and {len(rows) - MAX_LISTED} more, in the full review")
    return out + [""]


def comment_body(model: str, head: str, code: int, text: str, full: str, job_ok: bool = True) -> str:
    """The whole comment. `full` says where the complete review is; it is this job's own text."""
    job = review_job(model)
    v = verdict(code, text, job_ok)
    out = [mark(job), f"### AI review, {inline(model, 100)}: {v}", "",
           f"{SAYS[v]} Commit {inline(head, 40)}. The check {inline(job, 120)} is what the merger "
           "reads; this comment reports it.", ""]
    found = findings(text)
    if found is None:
        out += ["The review's list of findings could not be read.", ""]
    else:
        blocking, advisory = found
        if blocking:
            out += _section("Blocking", blocking)
        if advisory:
            out += _section("Advisory", advisory)
        if not blocking and not advisory:
            out += ["No findings.", ""]
    out += [full, ""]
    if v != PASS:
        # `<reason>` is shorter than the 20 characters a reason needs, so the line as it stands
        # is no override even with the class filled in.
        out += ["To release this block, a person in `humans` comments this one line with the class "
                "and the reason filled in (classes: `AGENTS.md`, Factory rules; a reason is 20 to 500 "
                "characters):", "",
                "```",
                f"/factory override ai-review {head} <class> model={model}: <reason>",
                "```", ""]
    return "\n".join(out).rstrip("\n") + "\n"


def summary(model: str, head: str, code: int, text: str, job_ok: bool = True) -> str:
    """The whole review for the job summary, inside a code fence its text cannot close."""
    text = text.replace("\x00", "")
    data = text.encode("utf-8")
    if len(data) > MAX_SUMMARY:
        text = data[:MAX_SUMMARY].decode("utf-8", "ignore") + "\n[cut here: the rest is in the job's artifact]"
    fence = "`" * (max([len(m) for m in re.findall(r"`+", text)] + [2]) + 1)
    return (f"## AI review, {inline(model, 100)}: {verdict(code, text, job_ok)}\n\n"
            f"Commit {inline(head, 40)}.\n\n{fence}text\n{text}\n{fence}\n")


# ---------------------------------------------------------------- the forge


class Target:
    """Where the comment goes on one forge, and what the forge itself says about this run."""

    def __init__(self, http, listing, one, edit, ours, head, full):
        self.http, self.listing, self.one, self.edit, self.ours = http, listing, one, edit, ours
        self.head, self.full = head, full
        self.mr_head = ""        # the merge request's head now, as the forge has it
        self.job_ok = False      # the forge reports the review job as succeeded


def target(env, job: str, transport=None):
    """The Target, or a string saying why there is nothing to do."""
    if env.get("GITLAB_CI") or env.get("CI_API_V4_URL"):
        token = env.get("FACTORY_COMMENT_TOKEN")
        if not token:
            return "FACTORY_COMMENT_TOKEN is not set in this job: no comment (go-live.md says how to set it)"
        forge = GitLab(env["CI_API_V4_URL"], env["CI_PROJECT_ID"], token, transport)
        mr = f"{forge.p}/merge_requests/{int(env['CI_MERGE_REQUEST_IID'])}"
        me = (forge.http.call("GET", "/user") or {}).get("id")

        def ours(c):
            return not c.get("system") and me is not None and (c.get("author") or {}).get("id") == me

        full = "Full review: the artifact `.review/` of the review job"
        if env.get("CI_PIPELINE_URL"):
            full += f", in [this pipeline]({env['CI_PIPELINE_URL']})"
        t = Target(forge.http, f"{mr}/notes", f"{mr}/notes", "PUT", ours, env.get("CI_COMMIT_SHA", ""), full + ".")
        t.mr_head = (forge.http.call("GET", mr) or {}).get("sha") or ""
        jobs = [j for j in forge.http.pages(f"{forge.p}/pipelines/{int(env['CI_PIPELINE_ID'])}/jobs")
                if j.get("name") == job]
        t.job_ok = bool(jobs) and max(jobs, key=lambda j: j.get("id") or 0).get("status") == "success"
        return t
    if env.get("GITHUB_ACTIONS") or env.get("GITHUB_REPOSITORY"):
        forge = GitHub(env.get("GITHUB_API_URL") or "https://api.github.com", env["GITHUB_REPOSITORY"],
                       env["GITHUB_TOKEN"], transport)
        number = int(env["FACTORY_PR_NUMBER"])

        def ours(c):
            return (c.get("user") or {}).get("login") == GITHUB_BOT

        full = "Full review: the summary of the job that posted this comment"
        if env.get("GITHUB_SERVER_URL") and env.get("GITHUB_RUN_ID"):
            full += (f", in [this run]({env['GITHUB_SERVER_URL']}/{env['GITHUB_REPOSITORY']}"
                     f"/actions/runs/{env['GITHUB_RUN_ID']})")
        t = Target(forge.http, f"{forge.r}/issues/{number}/comments", f"{forge.r}/issues/comments", "PATCH",
                   ours, env.get("FACTORY_HEAD_SHA", ""), full + ".")
        t.mr_head = (((forge.http.call("GET", f"{forge.r}/pulls/{number}") or {}).get("head")) or {}).get("sha") or ""
        t.job_ok = env.get("FACTORY_REVIEW_RESULT") == "success"   # `needs.<review job>.result`
        return t
    raise SystemExit("publish.py: not in GitLab CI or GitHub Actions (no CI_API_V4_URL / GITHUB_REPOSITORY)")


def publish(t: Target, job: str, body: str) -> str:
    """Edits this job's comment when there is one, posts it otherwise. Returns what it did."""
    first = mark(job)
    # Ours by its author AND its first line: anyone can write the marker into a comment. Two
    # runs that both found none both posted: every copy is edited, so none stays behind.
    mine = [c for c in t.http.pages(t.listing)
            if t.ours(c) and (c.get("body") or "").split("\n", 1)[0].strip() == first]
    for c in mine:
        t.http.call(t.edit, f"{t.one}/{int(c['id'])}", {"body": body})
    if not mine:
        t.http.call("POST", t.listing, {"body": body})
    return "edited" if mine else "posted"


def main(argv, env=os.environ, transport=None, out=sys.stdout) -> int:
    ap = argparse.ArgumentParser(prog="publish.py")
    ap.add_argument("--model", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--summary")
    a = ap.parse_args(argv[1:])
    if not MODEL.match(a.model):
        print("publish.py: --model is not a model name", file=sys.stderr)
        return 2
    job = review_job(a.model)
    try:
        t = target(env, job, transport)
    except ForgeError as e:
        print(f"publish.py: the forge refused this job's token (HTTP {e.status})", file=out)
        return 1
    except (KeyError, ValueError) as e:
        print(f"publish.py: this job's environment lacks {e}", file=sys.stderr)
        return 2
    if isinstance(t, str):
        print(f"publish.py: {t}", file=out)
        return 0
    if not SHA.match(t.head):
        print("publish.py: the head commit of this run is not known", file=sys.stderr)
        return 2
    got = read_result(a.dir, job[len("ai-review-"):], t.head)
    if got is None:
        print(f"publish.py: no result of {job} for {t.head}: nothing to post", file=out)
        return 0
    if t.mr_head != t.head:
        print(f"publish.py: {t.head} is not the merge request's head: nothing to post", file=out)
        return 0
    code, text = got
    try:
        body = comment_body(a.model, t.head, code, text, t.full, t.job_ok)
        if a.summary:
            with open(a.summary, "a", encoding="utf-8") as fh:
                fh.write(summary(a.model, t.head, code, text, t.job_ok))
        did = publish(t, job, body)
    except ForgeError as e:
        if e.status in (401, 403):
            # A pull request from a fork runs with a read-only token: no comment, not a failure.
            print(f"publish.py: this job's token may not comment here (HTTP {e.status}): no comment", file=out)
            if env.get("GITHUB_ACTIONS"):
                print("::warning::factory: the review's comment was not posted: the token cannot write "
                      "to this pull request (a fork?)", file=out)
            return 0
        print(f"publish.py: the forge answered HTTP {e.status}", file=out)
        return 1
    print(f"publish.py: {did} the comment of {job}: {verdict(code, text, t.job_ok)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
