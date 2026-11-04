#!/usr/bin/env python3
"""The backlog's state, for the backlog triage crons (`skills/kirocrew-factory/onboard.md`, Backlog).

The prompts `templates/backlog-triage-prompt.md` (issues) and `templates/backlog-pr-prompt.md`
(contributor merge requests) decide; this tool only reads the forge and says, deterministically,
which items still need a decision, which wait for a person and which wait for their author. It
writes one thing: the "Factory: waiting for you" issue (`page`).

Usage:
  python3 factory/backlog.py --forge gitlab|github [--url URL] --project PATH --token-env VAR
                             [--bot LOGIN ...] [--now ISO] COMMAND [options]

  list [--kind issues|prs|all] [--all] [--limit N]
      one JSON object per line, oldest first: the open items that need the agent's work now
      (`todo: true`), or every open item with --all. Fields: kind, number, ref, url, created,
      status, todo, decision, reason, action, title, author, labels; merge requests also head,
      draft, same_repo, mergeable, target.
  pr <n>
      one JSON object for one merge request: its status as in `list`, plus what the merger
      would do with it now (`verdict`: ready | needs-approval | wait | stale | blocked, and
      `reason`), its tier and the checks on its head. Read only.
  page [--dry-run]
      builds the "Factory: waiting for you" issue (one per repository, found by its marker)
      and creates it, or updates its description when it changed. --dry-run prints the body.

The rules come from `factory.yml` on the default branch, which must have `scope.mode: full`
(backlog triage takes every open item; in own mode the team's items are not the factory's).
Optional keys, all with defaults:

  backlog:
    batch: 10             # issues per triage run
    pr_batch: 5           # merge requests per run
    needs_info_days: 14   # an unanswered needs-info question becomes a proposed close
    abandoned_days: 30    # a merge request waiting for its author becomes a proposed decline

A decision is a comment ending with `<!-- factory-backlog decision=<d>[ key=value ...] -->`,
counted only from an account in `triager`, `crew` or `humans` (or a --bot): anyone else can type
the marker. The latest such comment on an item is its decision. Items carry these labels:
`factory: proposed close`, `factory: proposed merge`, `factory: proposed decline` (a person acts),
`factory: needs info` (the author answers). A decision whose label a person removed is a
declined proposal and goes back to the triage; one whose label was never put on goes back to
the triage to put it on.

Issue and merge request text is the reporters' and is printed as data (JSON strings); the page
strips `@` so that it notifies nobody.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fcore import (HOLD_LABELS, STALE, WAIT, Comment, ConfigError, ForgeError, check_mr,  # noqa: E402
                   match_any, mr_tier, parse_config, ts)
from forges import _Http, github_api, make_forge, q  # noqa: E402

LABEL_CLOSE = "factory: proposed close"
LABEL_MERGE = "factory: proposed merge"
LABEL_DECLINE = "factory: proposed decline"
LABEL_INFO = "factory: needs info"
NEEDS_HUMAN = "crew: needs human"
MAIN_RED = "factory: main red"
PAGE_TITLE = "Factory: waiting for you"
PAGE_MARKER = "<!-- factory-backlog-page -->"
PAGE_LIMIT = 60000          # GitHub's issue body cap is 65536 characters

ISSUE_DECISIONS = {"tier", "duplicate", "already-fixed", "needs-info", "not-reproducible", "by-design",
                   "abandoned"}
PR_DECISIONS = {"proposed-merge", "needs-changes", "proposed-decline", "needs-run", "checking"}
CLOSE = {"duplicate", "already-fixed", "not-reproducible", "by-design", "abandoned"}
# The label a decision puts on, and what the person then does.
LABEL_OF = {**{d: LABEL_CLOSE for d in CLOSE}, "needs-info": LABEL_INFO, "needs-changes": LABEL_INFO,
            "proposed-merge": LABEL_MERGE, "proposed-decline": LABEL_DECLINE}
ACTION_OF = {**{d: "close" for d in CLOSE}, "proposed-merge": "merge", "proposed-decline": "decline",
             "needs-run": "run checks"}
ACTIONS = ["close", "merge", "decline", "approve design", "run checks", "answer"]
ACTION_HOW = {
    "close": "close it with the prepared comment in its last factory comment, or remove "
             f"`{LABEL_CLOSE}` and say why",
    "merge": "approve and merge it, or remove `{merge}` and say why".format(merge=LABEL_MERGE),
    "decline": "close it with the prepared comment, or remove `{d}` and say why".format(d=LABEL_DECLINE),
    "approve design": "read the design and approve the merge request, or comment what to change",
    "run checks": "read the diff, then decide how its checks run: on GitLab a fork's pipeline "
                  "here runs its code with this project's CI variables, on GitHub a fork's "
                  "workflows get no secrets, so the factory does not start them",
    "answer": f"answer the crew's question on the item, then remove `{NEEDS_HUMAN}`",
}

MARK = re.compile(r"<!--\s*factory-backlog\s+((?:[a-z_]+=[^\s>]+\s*)+)-->")
_HEAD_LINE = re.compile(r"^\s*\*\*\s*Backlog:\s*(?P<what>.+?)\s*\*\*\s*(?P<why>.*)$", re.I)


class BacklogError(Exception):
    pass


STEP_BRANCH = re.compile(r"(?im)^\s*Feature branch:\s*\S+")
STEP_PARENT = re.compile(r"(?i)\bPart of #\d+")


class Item(NamedTuple):
    kind: str               # issue | mr
    number: int
    title: str
    author: str
    created: str
    updated: str
    labels: tuple
    url: str
    comments: int = -1      # count from the list, -1 unknown
    body: str = ""
    mr: object = None       # fcore.MR for a merge request


class Decision(NamedTuple):
    decision: str
    attrs: dict
    at: str
    author: str
    what: str               # the bold head line: "duplicate of #9"
    why: str                # the rest of that line


class State(NamedTuple):
    item: Item
    status: str
    todo: bool
    decision: Decision | None = None
    action: str = ""
    note: str = ""

    def row(self, forge_kind: str) -> dict:
        it = self.item
        ref = (("!" if forge_kind == "gitlab" else "#") if it.kind == "mr" else "#") + str(it.number)
        out = {"kind": it.kind, "number": it.number, "ref": ref, "url": it.url, "created": it.created,
               "status": self.status, "todo": self.todo,
               "decision": self.decision.decision if self.decision else "",
               "reason": one_line(self.decision.what + (": " + self.decision.why if self.decision.why else ""))
               if self.decision else "", "action": self.action, "note": self.note,
               "title": it.title, "author": it.author, "labels": list(it.labels)}
        if it.mr is not None:
            out.update(head=it.mr.head, draft=it.mr.draft, same_repo=it.mr.same_repo,
                       mergeable=it.mr.mergeable, target=it.mr.target)
        return out


def one_line(text: str, limit: int = 160) -> str:
    """Text as one line that links and notifies nobody: no `@`, no HTML comment, no newline."""
    t = re.sub(r"\s+", " ", (text or "").replace("<!--", "").replace("-->", "")).strip()
    t = t.replace("@", "")
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def parse_decision(c: Comment) -> Decision | None:
    found = None
    for m in MARK.finditer(c.body or ""):
        found = m
    if not found:
        return None
    attrs = dict(kv.split("=", 1) for kv in found.group(1).split())
    d = attrs.pop("decision", "")
    if d not in ISSUE_DECISIONS | PR_DECISIONS:
        return None
    what, why = d, ""
    for line in (c.body or "").splitlines():
        m = _HEAD_LINE.match(line)
        if m:
            what, why = m["what"].rstrip(".").strip(), m["why"].strip()
            break
    return Decision(d, attrs, c.created_at, c.author, what, why)


def latest_decision(comments: list[Comment], trusted: set[str]) -> Decision | None:
    out = None
    for c in sorted(comments, key=lambda c: c.created_at):
        if c.author in trusted:
            d = parse_decision(c)
            if d is not None:
                out = d
    return out


# ---------------------------------------------------------------- reading the forge


class Reader:
    """What the backlog reads beyond `fcore.Forge`: open issues and merge requests with their
    dates, and an issue's comments. GitLab REST v4 and GitHub REST."""

    def __init__(self, kind: str, url: str, project: str, token: str, transport=None):
        self.kind = kind
        self.web = url.rstrip("/")
        self.project = project
        self.forge = make_forge(kind, url, project, token, transport)
        if kind == "gitlab":
            self.http = _Http(self.web + "/api/v4", {"PRIVATE-TOKEN": token}, transport)
            self.p = f"/projects/{q(project)}"
        else:
            self.http = _Http(github_api(url), {"Authorization": f"Bearer {token}",
                                                "Accept": "application/vnd.github+json",
                                                "X-GitHub-Api-Version": "2022-11-28"}, transport)
            owner, repo = project.split("/", 1)
            self.p = f"/repos/{q(owner)}/{q(repo)}"

    def item_url(self, kind: str, n: int) -> str:
        base = f"{self.web}/{self.project}"
        if self.kind == "gitlab":
            return f"{base}/-/{'merge_requests' if kind == 'mr' else 'issues'}/{n}"
        return f"{base}/{'pull' if kind == 'mr' else 'issues'}/{n}"

    def open_issues(self) -> list[Item]:
        if self.kind == "gitlab":
            rows = self.http.pages(f"{self.p}/issues?state=opened&order_by=created_at&sort=asc")
            items = [Item("issue", int(i["iid"]), i.get("title") or "", (i.get("author") or {}).get("username", ""),
                          i.get("created_at") or "", i.get("updated_at") or "", tuple(i.get("labels") or ()),
                          i.get("web_url") or self.item_url("issue", int(i["iid"])),
                          int(i.get("user_notes_count", -1)), i.get("description") or "") for i in rows]
        else:
            rows = self.http.pages(f"{self.p}/issues?state=open&sort=created&direction=asc")
            items = [Item("issue", int(i["number"]), i.get("title") or "", (i.get("user") or {}).get("login", ""),
                          i.get("created_at") or "", i.get("updated_at") or "",
                          tuple(x["name"] if isinstance(x, dict) else str(x) for x in i.get("labels") or ()),
                          i.get("html_url") or self.item_url("issue", int(i["number"])),
                          int(i.get("comments", -1)), i.get("body") or "")
                     for i in rows if "pull_request" not in i]
        return sorted(items, key=lambda i: (i.created, i.number))

    def open_mrs(self) -> list[Item]:
        if self.kind == "gitlab":
            rows = self.http.pages(f"{self.p}/merge_requests?state=opened&order_by=created_at&sort=asc")
            num, cnt = "iid", "user_notes_count"
        else:
            rows = self.http.pages(f"{self.p}/pulls?state=open&sort=created&direction=asc")
            num, cnt = "number", "comments"
        out = []
        for r in rows:
            n = int(r[num])
            mr = self.forge.get_mr(n)  # mergeability and the head, as the merger reads them
            out.append(Item("mr", n, mr.title, mr.author, r.get("created_at") or "", r.get("updated_at") or "",
                            tuple(mr.labels), r.get("web_url") or r.get("html_url") or self.item_url("mr", n),
                            int(r.get(cnt, -1)), mr.body, mr))
        return sorted(out, key=lambda i: (i.created, i.number))

    def comments(self, item: Item) -> list[Comment]:
        if item.comments == 0:
            return []
        if item.kind == "mr":
            return self.forge.comments(item.mr)
        if self.kind == "gitlab":
            notes = self.http.pages(f"{self.p}/issues/{item.number}/notes?sort=asc&order_by=created_at")
            out = [Comment((n.get("author") or {}).get("username", ""), n.get("body") or "",
                           n.get("created_at") or "") for n in notes if not n.get("system")]
        else:
            rows = self.http.pages(f"{self.p}/issues/{item.number}/comments")
            out = [Comment((c.get("user") or {}).get("login", ""), c.get("body") or "", c.get("created_at") or "")
                   for c in rows]
        return sorted(out, key=lambda c: c.created_at)

    def label_events(self, item: Item) -> list[dict]:
        if item.kind == "mr":
            return self.forge.mr_label_events(item.mr)
        return self.forge.label_events(item.number)


# ---------------------------------------------------------------- the rules


class Settings(NamedTuple):
    batch: int = 10
    pr_batch: int = 5
    needs_info_days: float = 14
    abandoned_days: float = 30


def settings(raw: dict) -> Settings:
    b = raw.get("backlog") or {}
    if not isinstance(b, dict):
        raise ConfigError("backlog must be a mapping")
    out = {}
    for key, default in Settings._field_defaults.items():
        v = b.get(key, default)
        whole = isinstance(default, int) and not isinstance(default, bool) and key.endswith("batch")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0 or (whole and v != int(v)):
            raise ConfigError(f"backlog.{key} must be a {'whole ' if whole else ''}number above 0")
        out[key] = int(v) if whole else v
    return Settings(**out)


class _AssumeApproved:
    """The forge as if a person in `humans` approved the head: what blocks besides approval."""

    def __init__(self, forge, humans):
        self._f, self._h = forge, set(humans)

    def approvers(self, mr):
        return set(self._h)

    def __getattr__(self, name):
        return getattr(self._f, name)


class Backlog:
    def __init__(self, reader: Reader, bots=(), now: datetime.datetime | None = None):
        self.r = reader
        self.forge = reader.forge
        self.now = now or datetime.datetime.now(datetime.timezone.utc)
        branch = self.forge.default_branch()
        text = self.forge.read_file("factory.yml", branch)
        if text is None:
            raise BacklogError(f"no factory.yml on {branch}")
        self.cfg = parse_config(text)
        if self.cfg.scope_mode != "full":
            raise BacklogError("backlog triage runs in full mode only (scope.mode is "
                               f"{self.cfg.scope_mode!r}): in own mode the team's items are not the factory's")
        self.settings = settings(self.cfg.raw)
        self.trusted = self.cfg.triager | self.cfg.crew | self.cfg.humans | {b for b in bots if b}
        if not (self.cfg.triager | self.cfg.crew | {b for b in bots if b}):
            raise BacklogError("factory.yml lists no triager or crew account (or pass --bot): "
                               "a decision comment is counted only from those")
        self.tier_labels = {t["label"] for t in self.cfg.tier.values()}

    def _days_since(self, iso: str) -> float:
        t = ts(iso)
        return 0.0 if t is None else (self.now - t).total_seconds() / 86400

    def _label_state(self, item: Item, label: str, since: str) -> str:
        """`on`, `removed` (a person took it off after the decision) or `missing` (never put on)."""
        if label in item.labels:
            return "on"
        if label == "tier" and self.tier_labels & set(item.labels):
            return "on"
        names = self.tier_labels if label == "tier" else {label}
        start = ts(since)
        for e in self.r.label_events(item):
            t = ts(e.get("created_at"))
            if e.get("action") == "remove" and e.get("label") in names and (start is None or t is None or t >= start):
                return "removed"
        return "missing"

    def is_page(self, item: Item) -> bool:
        return item.kind == "issue" and PAGE_MARKER in item.body and item.author in self.trusted

    def skipped(self, item: Item) -> bool:
        if self.is_page(item) or MAIN_RED in item.labels:
            return True
        if item.kind == "mr":
            return item.author in self.cfg.crew or item.mr.target != self.cfg.default_branch
        return self.is_step(item)

    def is_step(self, item: Item) -> bool:
        """A step issue the crew opened from an approved design: the design already decided it."""
        return (item.kind == "issue" and item.author in self.cfg.crew
                and bool(STEP_BRANCH.search(item.body)) and bool(STEP_PARENT.search(item.body)))

    def state(self, item: Item) -> State:
        if NEEDS_HUMAN in item.labels:
            return State(item, "waiting-you", False, None, "answer")
        comments = self.r.comments(item)
        d = latest_decision(comments, self.trusted)
        if item.kind == "issue":
            return self._issue_state(item, comments, d)
        return self._mr_state(item, comments, d)

    def _author_replied(self, item: Item, comments: list[Comment], d: Decision) -> bool:
        start = ts(d.at)
        return any(c.author == item.author and c.author not in self.trusted - self.cfg.humans
                   and (start is None or (ts(c.created_at) or start) > start) for c in comments)

    def _labelled(self, item: Item, d: Decision, label: str):
        """None when the decision's label is on, else the State that sends it back to triage."""
        got = self._label_state(item, label, d.at)
        if got == "on":
            return None
        if got == "removed":
            return State(item, "declined", True, d, "",
                         "a person removed the label: read their comment and decide again")
        return State(item, "unlabelled", True, d, "", f"put the label on: {label if label != 'tier' else 'the tier'}")

    def _issue_state(self, item: Item, comments: list[Comment], d: Decision | None) -> State:
        if d is None or d.decision not in ISSUE_DECISIONS:
            if self.tier_labels & set(item.labels):
                return State(item, "tiered", False, None, "", "tiered before the backlog triage")
            if any("<!-- factory-triage -->" in c.body and c.author in self.trusted for c in comments):
                return State(item, "tiered", False, None, "", "the triager took it")
            return State(item, "untriaged", True)
        if d.decision == "tier":
            back = self._labelled(item, d, "tier")
            return back or State(item, "settled", False, d, "", "tiered: the crew's")
        if d.decision == "needs-info":
            back = self._labelled(item, d, LABEL_INFO)
            if back:
                return back
            if self._author_replied(item, comments, d):
                return State(item, "answered", True, d, "", "the author answered: decide again")
            if self._days_since(d.at) >= self.settings.needs_info_days:
                return State(item, "stale", True, d, "",
                             f"no answer in {self.settings.needs_info_days:g} days: propose a close (abandoned)")
            return State(item, "waiting-author", False, d)
        back = self._labelled(item, d, LABEL_CLOSE)
        return back or State(item, "waiting-you", False, d, ACTION_OF[d.decision])

    def _mr_state(self, item: Item, comments: list[Comment], d: Decision | None) -> State:
        mr = item.mr
        if d is None or d.decision not in PR_DECISIONS:
            if mr.draft and self._days_since(item.updated) < self.settings.abandoned_days:
                return State(item, "waiting-author", False, None, "", "draft")
            return State(item, "untriaged", True)
        if d.attrs.get("head") and d.attrs["head"] != mr.head:
            return State(item, "new-head", True, d, "", "the author pushed since the decision: decide again")
        if d.decision == "checking":
            return State(item, "checking", True, d, "", "checks were started: read the result")
        if d.decision == "needs-run":
            return State(item, "waiting-you", False, d, "run checks")
        if d.decision == "needs-changes":
            back = self._labelled(item, d, LABEL_INFO)
            if back:
                return back
            if self._author_replied(item, comments, d):
                return State(item, "answered", True, d, "", "the author answered: decide again")
            if self._days_since(d.at) >= self.settings.abandoned_days:
                return State(item, "stale", True, d, "",
                             f"no push or answer in {self.settings.abandoned_days:g} days: propose a decline (abandoned)")
            return State(item, "waiting-author", False, d)
        back = self._labelled(item, d, LABEL_OF[d.decision])
        return back or State(item, "waiting-you", False, d, ACTION_OF[d.decision])

    # -------- commands

    def items(self, kind: str = "all") -> list[Item]:
        out = []
        if kind in ("issues", "all"):
            out += self.r.open_issues()
        if kind in ("prs", "all"):
            out += self.r.open_mrs()
        return sorted(out, key=lambda i: (i.created, i.kind, i.number))

    def states(self, kind: str = "all") -> list[State]:
        return [self.state(i) for i in self.items(kind) if not self.skipped(i)]

    def pr(self, number: int) -> dict:
        mr = self.forge.get_mr(number)
        items = [i for i in self.r.open_mrs() if i.number == number]
        if not items:
            raise BacklogError(f"no open merge request {number}")
        item = items[0]
        out = self.state(item).row(self.forge.kind)
        if item.author in self.cfg.crew:
            out["note"] = "the crew's: not backlog"
        tier, why = mr_tier(self.forge, mr, self.cfg)
        reason, _note = check_mr(self.forge, mr, self.cfg)
        if reason is None:
            verdict = "ready"
        elif reason == WAIT:
            verdict = "wait"
        elif reason == STALE:
            verdict = "stale"
        else:
            other, _n = check_mr(_AssumeApproved(self.forge, self.cfg.humans), mr, self.cfg)
            verdict = "needs-approval" if other is None else "blocked"
            reason = reason if other is None else other
        checks = self.forge.checks(mr) or []
        out.update(verdict=verdict, verdict_reason=reason or "", tier=tier, tier_reason=why,
                   held=sorted(HOLD_LABELS & set(mr.labels)),
                   checks=[{"name": c.name, "status": c.status} for c in checks])
        return out

    def design_mrs(self, items: list[Item]) -> list[State]:
        """The crew's open merge requests that carry a design, not yet approved by a person."""
        out = []
        for item in (i for i in items if i.kind == "mr"):
            if item.author not in self.cfg.crew or item.mr.draft:
                continue
            paths = self.forge.changed_paths(item.mr)
            if any(match_any(p, [self.cfg.designs_dir + "/**"]) for p in paths) and \
                    not (self.forge.approvers(item.mr) & self.cfg.humans):
                out.append(State(item, "waiting-you", False, None, "approve design"))
        return out

    def page_body(self, items: list[Item]) -> str:
        states = [self.state(i) for i in items if not self.skipped(i)]
        waiting = [s for s in states if s.status == "waiting-you"] + self.design_mrs(items)
        n_issues = sum(1 for s in states if s.item.kind == "issue")
        n_mrs = len(states) - n_issues
        authors = sum(1 for s in states if s.status == "waiting-author")
        todo = sum(1 for s in states if s.todo)
        done = sum(1 for s in states if s.status in ("settled", "tiered"))
        lines = [f"Everything in this repository's backlog that waits for a person, one line each with "
                 f"the one action that settles it. The factory updates this description on each "
                 f"backlog run; act on the linked item, not here.",
                 "",
                 f"**Open:** {n_issues} issues, {n_mrs} contributor "
                 f"{'merge requests' if self.forge.kind == 'gitlab' else 'pull requests'}. "
                 f"**Waiting for you:** {len(waiting)}. **Waiting for their authors:** {authors}. "
                 f"**With the crew:** {done}. **Not decided yet:** {todo}.",
                 ""]
        mr_sig = "!" if self.forge.kind == "gitlab" else "#"
        for action in ACTIONS:
            group = sorted((s for s in waiting if s.action == action), key=lambda s: (s.item.created, s.item.number))
            if not group:
                continue
            lines += [f"### {action[0].upper() + action[1:]} ({len(group)})", "", f"_{ACTION_HOW[action]}._", ""]
            for s in group:
                ref = (mr_sig if s.item.kind == "mr" else "#") + str(s.item.number)
                if s.decision:
                    why = s.decision.what + (": " + s.decision.why if s.decision.why else "")
                else:
                    why = s.item.title
                lines.append(f"- {ref} {one_line(why)}")
            lines.append("")
        if not waiting:
            lines += ["Nothing waits for you now.", ""]
        lines.append(f"Updated {self.now.strftime('%Y-%m-%d %H:%M')} UTC.")
        body = "\n".join(lines)
        if len(body) > PAGE_LIMIT:
            cut = body[:PAGE_LIMIT].rsplit("\n", 1)[0]
            body = cut + "\n\n… the list goes on: run `backlog.py list --all` for the rest.\n"
        return body + "\n\n" + PAGE_MARKER + "\n"

    def page(self, dry_run: bool = False) -> tuple[str, int | None, str]:
        items = self.items()
        pages = sorted((i for i in items if self.is_page(i)), key=lambda i: i.number)
        body = self.page_body(items)
        if dry_run:
            return "dry-run", pages[0].number if pages else None, body
        if not pages:
            return "created", self.forge.create_issue(PAGE_TITLE, body, []), body
        old = pages[0].body
        if _without_stamp(old) == _without_stamp(body):
            return "unchanged", pages[0].number, body
        self.forge.update_issue(pages[0].number, body)
        return "updated", pages[0].number, body


def _without_stamp(body: str) -> str:
    return re.sub(r"(?m)^Updated \d{4}-\d\d-\d\d \d\d:\d\d UTC\.$", "", (body or "").replace("\r\n", "\n")).strip()


# ---------------------------------------------------------------- CLI


def parse_args(argv):
    p = argparse.ArgumentParser(prog="backlog.py", description="The backlog's state for the backlog triage.")
    p.add_argument("--forge", required=True, choices=("gitlab", "github"))
    p.add_argument("--url", default="", help="web address (default: gitlab.com or github.com)")
    p.add_argument("--project", required=True)
    p.add_argument("--token-env", required=True, help="the environment variable holding the token")
    p.add_argument("--bot", action="append", default=[], help="an account whose decisions count besides "
                                                                 "factory.yml triager, crew and humans")
    p.add_argument("--now", default="", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="command", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--kind", choices=("issues", "prs", "all"), default="all")
    ls.add_argument("--all", action="store_true", help="every open item, not only those to work on")
    ls.add_argument("--limit", type=int, default=0)
    pr = sub.add_parser("pr")
    pr.add_argument("number", type=int)
    pg = sub.add_parser("page")
    pg.add_argument("--dry-run", action="store_true")
    return p.parse_args(argv)


def main(argv=None, env=None, transport=None, out=None) -> int:
    a = parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    out = out or sys.stdout
    token = env.get(a.token_env, "")
    if not token:
        print(f"backlog.py: ${a.token_env} is empty", file=sys.stderr)
        return 2
    url = a.url or ("https://gitlab.com" if a.forge == "gitlab" else "https://github.com")
    now = ts(a.now) if a.now else None
    try:
        b = Backlog(Reader(a.forge, url, a.project.strip("/"), token, transport), a.bot, now)
        if a.command == "list":
            n = 0
            for s in b.states(a.kind):
                if not (a.all or s.todo):
                    continue
                print(json.dumps(s.row(a.forge), ensure_ascii=False), file=out)
                n += 1
                if a.limit and n >= a.limit:
                    break
        elif a.command == "pr":
            print(json.dumps(b.pr(a.number), ensure_ascii=False), file=out)
        else:
            what, number, body = b.page(a.dry_run)
            if what == "dry-run":
                print(body, file=out)
            else:
                print(f"page #{number} {what}", file=out)
    except (BacklogError, ConfigError, ForgeError) as e:
        print(f"backlog.py: {str(e).replace(token, '***')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
