#!/usr/bin/env python3
"""Show an AI review where people look: the job summary, and one comment on the pull request.

    publish.py summary <review.md> <model> <head sha>
    publish.py comment <review.md> <model> <head sha>   (GitHub; reads GITHUB_TOKEN,
                                                         GITHUB_REPOSITORY, PR_NUMBER, JOB_URL)

The review job writes the summary itself: it needs no write right. The comment comes from a
separate job (templates/github-workflow-factory.yml) that holds `pull-requests: write` and no
KIRO_API_KEY, and runs nothing from the pull request: the token that may write to the pull
request is never in the job where the model reads untrusted text.

One comment per model, found again by its first line `<!-- factory-review model=<model> -->`
and the author `github-actions[bot]`, and edited for each new head. A head that is no longer
the pull request's head is not published: the run for the newer head publishes. Nothing the
factory decides reads this comment: overrides and dispositions count only from a person in
`humans` or the pull request's author (fcore.parse_overrides, review/prepare.py ledger).

Every failure to publish is a warning and exit 0: showing a review must never turn a check
red or block a merge. Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

COMMENT_MAX = 60000      # GitHub refuses a comment over 65536 characters
SUMMARY_MAX = 900000     # a step summary is cut at 1 MiB
FIELD_MAX = 1500         # one finding field; the full text stays in the job summary
BOT = "github-actions[bot]"
SECTIONS = (("blocking", "Blocking"), ("advisory", "Advisory"))
OVERRIDE_CLASSES = ("false-positive", "not-applicable", "owner-decision",
                    "pre-existing #<issue>", "deferred #<open issue>", "infra-failure <job URL>")
# A person owns these anchors: owner-decision is the class that fits them.
OWNER_ANCHORS = {"product-shape", "design-one-way-door", "evidence-gap"}


def marker(model: str) -> str:
    return f"<!-- factory-review model={model} -->"


def safe(text, limit: int = FIELD_MAX) -> str:
    """Model text as inert markdown: no mentions, no HTML comments, no line breaks, bounded."""
    s = " ".join(str(text or "").split())
    s = s.replace("@", "@\u200b").replace("<!--", "&lt;!--")
    return s if len(s) <= limit else s[: limit - 1] + "\u2026"


def code(text, limit: int = 300) -> str:
    s = safe(text, limit).replace("`", "'")
    return f"`{s}`" if s else ""


def parse(text: str) -> dict:
    """{verdict, findings, lens_errors}; verdict is PASS, BLOCK or '' when the review did not end."""
    verdict = ""
    for line in reversed([ln.strip() for ln in text.splitlines() if ln.strip()]):
        m = re.fullmatch(r"VERDICT:\s*(PASS|BLOCK)", line)
        if m:
            verdict = m.group(1)
        break
    data = {}
    blocks = re.findall(r"```json\s*\n(.*?)\n```", text, re.S)
    if blocks:
        try:
            data = json.loads(blocks[-1])
        except ValueError:
            data = {}
    findings = data.get("findings") if isinstance(data, dict) else None
    errors = data.get("lens_errors") if isinstance(data, dict) else None
    return {"verdict": verdict,
            "findings": [f for f in findings or [] if isinstance(f, dict)],
            "lens_errors": [e for e in errors or []] if isinstance(errors, list) else []}


def finding_lines(n: int, f: dict) -> list[str]:
    where = f.get("file") or ""
    if where and f.get("line"):
        where = f"{where}:{f.get('line')}"
    head = f"{n}. **{safe(f.get('headline'), 300)}**"
    tags = [x for x in (code(where), f"anchor {code(f.get('anchor'), 60)}" if f.get("anchor") else "") if x]
    out = [head + (" \u2014 " + " \u00b7 ".join(tags) if tags else "")]
    for label, key in (("What goes wrong", "consequence"), ("Fix", "fix"), ("Clears when", "clears_when")):
        if f.get(key):
            out.append(f"   - {label}: {safe(f.get(key))}")
    sites = [code(s, 200) for s in (f.get("sites") or [])[:12] if isinstance(s, str)]
    if sites:
        out.append("   - Also at: " + ", ".join(sites))
    return out


def comment_body(text: str, model: str, head: str, job_url: str) -> str:
    r = parse(text)
    short = head[:8]
    state = {"PASS": "PASS", "BLOCK": "BLOCK"}.get(r["verdict"], "did not finish")
    out = [marker(model), f"### AI review `{model}`: {state} at `{short}`", ""]
    if not r["verdict"]:
        out += ["The review reached no verdict (an infrastructure failure, a timeout or spent "
                "credits). Its check stays red; the job log says why.", ""]
    by_sev: dict[str, list[dict]] = {}
    for f in r["findings"]:
        by_sev.setdefault(str(f.get("severity") or "other").lower(), []).append(f)
    names = dict(SECTIONS)
    order = [s for s, _ in SECTIONS] + sorted(k for k in by_sev if k not in names)
    for sev in order:
        items = by_sev.get(sev) or []
        title = names.get(sev, safe(sev, 40).capitalize())
        out.append(f"#### {title} ({len(items)})")
        if not items:
            out += ["None.", ""]
            continue
        for i, f in enumerate(items, 1):
            out += finding_lines(i, f)
        out.append("")
    if r["lens_errors"]:
        out += ["#### Lenses that did not run", ""]
        out += [f"- {safe(e, 300)}" for e in r["lens_errors"][:10]] + [""]
    if r["verdict"] == "BLOCK":
        anchors = {str(f.get("anchor") or "") for f in by_sev.get("blocking", [])}
        cls = "owner-decision" if anchors and anchors <= OWNER_ANCHORS else "<class>"
        out += ["#### Release this block at this head",
                "A person in `humans` posts, with a reason of 20-500 characters "
                f"(classes: {', '.join(f'`{c}`' for c in OVERRIDE_CLASSES)}):", "",
                "```",
                f"/factory override ai-review {head} {cls} model={model}: <reason>",
                "```", ""]
    links = [f"[job log and full review]({job_url})"] if job_url else []
    out.append("Full review: the job summary of this check" + (f", {links[0]}" if links else "") + ".")
    body = "\n".join(out)
    if len(body) > COMMENT_MAX:
        body = body[: COMMENT_MAX - 200] + "\n\n\u2026 cut to fit a comment; the full review is in the job summary."
    return body


def summary_text(text: str, model: str, head: str) -> str:
    """The whole review for the job summary, without the agent's tool trace."""
    keep = [ln for ln in text.splitlines() if not ln.startswith("[tool] ")]
    body = "\n".join(keep).strip()
    body = body.replace("@", "@\u200b")
    if len(body) > SUMMARY_MAX:
        body = body[:SUMMARY_MAX] + "\n\n\u2026 cut; the review artifact holds the rest."
    return f"## AI review `{model}` at `{head[:8]}`\n\n{body}\n"


def _api(method: str, url: str, token: str, data=None):
    req = urllib.request.Request(url, method=method, data=None if data is None else json.dumps(data).encode(),
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json",
                                          "X-GitHub-Api-Version": "2022-11-28",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        out = r.read()
        return json.loads(out) if out.strip() else None, r.headers.get("Link", "")


def publish(body: str, model: str, head: str, env=os.environ, api=_api) -> str:
    token, repo, pr = env.get("GITHUB_TOKEN", ""), env.get("GITHUB_REPOSITORY", ""), env.get("PR_NUMBER", "")
    if not (token and repo and pr.isdigit()):
        return "skipped: GITHUB_TOKEN, GITHUB_REPOSITORY or PR_NUMBER missing"
    base = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/") + f"/repos/{repo}"
    current, _ = api("GET", f"{base}/pulls/{pr}", token)
    if (current or {}).get("head", {}).get("sha", "").lower() != head.lower():
        return "skipped: the pull request has moved to a newer head; that run publishes"
    mine = None
    url = f"{base}/issues/{pr}/comments?per_page=100"
    while url and mine is None:
        rows, link = api("GET", url, token)
        for c in rows or []:
            if (c.get("user") or {}).get("login") == BOT and (c.get("body") or "").startswith(marker(model)):
                mine = c
                break
        m = re.search(r'<([^>]+)>;\s*rel="next"', link or "")
        url = m.group(1) if m else ""
    if mine:
        api("PATCH", f"{base}/issues/comments/{mine['id']}", token, {"body": body})
        return f"updated comment {mine['id']}"
    api("POST", f"{base}/issues/{pr}/comments", token, {"body": body})
    return "posted a new comment"


def main(argv: list[str]) -> int:
    if len(argv) != 5 or argv[1] not in ("summary", "comment"):
        print(__doc__.split("\n\n")[1])
        return 2
    mode, path, model, head = argv[1:]
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError:
        text = ""
    try:
        if mode == "summary":
            out = os.environ.get("GITHUB_STEP_SUMMARY")
            if out:
                with open(out, "a", encoding="utf-8") as fh:
                    fh.write(summary_text(text, model, head))
            print("review summary written" if out else "no GITHUB_STEP_SUMMARY: nothing written")
        else:
            print("review comment: " + publish(comment_body(text, model, head, os.environ.get("JOB_URL", "")),
                                               model, head))
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError) as e:
        print(f"::warning::the {model} review could not be shown on the pull request: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
