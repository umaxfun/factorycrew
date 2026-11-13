"""The factory's rules, shared by the merger cron and the CI jobs. No network here.

Everything a rule needs from the forge comes through the `Forge` interface at the
bottom of this file; `forges.py` implements it for GitLab and GitHub. The merger
(`merger/merger.py`) and the CI tier job (`factory/tier.py`) both call
`mr_tier` and the merger calls `check_mr`, so the two compute the same thing.

Standard library only. PyYAML is needed to read factory.yml; without it
`load_yaml` raises ConfigError naming the package to install.
"""

from __future__ import annotations

import datetime
import re
from typing import NamedTuple

# ---------------------------------------------------------------- config


class ConfigError(Exception):
    """factory.yml is missing a key, has a wrong value, or cannot be parsed."""


def load_yaml(text: str) -> dict:
    try:
        import yaml  # noqa: PLC0415 - optional dependency
    except ImportError as e:  # pragma: no cover - depends on the host
        raise ConfigError("PyYAML is not installed: `pip install pyyaml` or "
                          "`apt-get install python3-yaml`") from e
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"factory.yml does not parse: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError("factory.yml must be a mapping at the top level")
    return data


HOLD_LABELS = {"crew: needs human", "hold"}
DEFAULT_APP = "github-actions"
FACTORY_WORKFLOW = ".github/workflows/factory.yml"
BUILTIN_CHECKS = ("ai-review", "factory-docs")
# What a tier's change does to the product, low to high (see schema.md, tiers).
EFFECTS = ("fix", "addition", "change", "concept")
DESIGNS = ("short", "full")
LENSES = ("correctness", "security", "first_principles", "tests_spec", "ux", "public_api")


def review_job(model: str) -> str:
    """CI job name of the review on one model: ai-review-<slug>. review.sh uses the same slug."""
    return "ai-review-" + re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def comment_job(model: str) -> str:
    """CI job name of the job that shows that review on the merge request: review-comment-<slug>."""
    return "review-comment-" + review_job(model)[len("ai-review-"):]


class CheckSpec(NamedTuple):
    """One concrete check the merger looks for. `review` marks an AI review job."""
    name: str           # the factory.yml name it came from (ai-review expands to two)
    job: str            # job / check-run / status context name
    workflow: str = ""  # GitHub: workflow file the check-run must come from
    app: str = ""       # GitHub: app slug of the check-run ("commit-status" for statuses)
    review: bool = False


def _list(v, key: str) -> list:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if not isinstance(v, list):
        raise ConfigError(f"{key} must be a list")
    return list(v)


def _number(v, key: str, whole: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0 or (whole and v != int(v)):
        raise ConfigError(f"{key} must be a {'whole ' if whole else ''}number, 0 or more")
    return int(v) if whole else v


class Config:
    """A validated factory.yml. `raw` keeps the parsed mapping."""

    def __init__(self, raw: dict):
        self.raw = raw
        if raw.get("version") != 1:
            raise ConfigError("version must be 1")
        forge = raw.get("forge") or {}
        self.forge_kind = forge.get("kind")
        if self.forge_kind not in ("gitlab", "github"):
            raise ConfigError("forge.kind must be gitlab or github")
        self.forge_url = forge.get("url") or ""
        self.project = forge.get("project") or ""
        self.merge_method = forge.get("merge_method") or "merge"
        self.default_branch = raw.get("default_branch") or "main"
        self.humans = {str(h) for h in _list(raw.get("humans"), "humans")}
        # The crew's forge accounts. Set: only a merge request one of them opened counts as the
        # crew's (its author may post a `false-positive` override). Empty: any author who is
        # not in `humans` does, except on an adopted merge request.
        self.crew = {str(h) for h in _list(raw.get("crew"), "crew")}
        if self.crew & self.humans:
            raise ConfigError("crew and humans must not share an account")
        # The triager's forge accounts. Besides `humans`, only they may set an issue's first
        # tier label: from anyone else a tier below the highest does not count.
        self.triager = {str(h).strip() for h in _list(raw.get("triager"), "triager")}
        if "" in self.triager:
            raise ConfigError("triager must list forge accounts, not empty names")
        if self.triager & self.humans:
            raise ConfigError("triager and humans must not share an account")
        review = raw.get("review") or {}
        self.review_models = [str(m) for m in _list(review.get("models"), "review.models")]
        self.calibration = review.get("calibration") or "factory/review-calibration.md"
        self.max_rounds = int(review.get("max_rounds") or 2)
        evidence = raw.get("evidence") or {}
        self.screenshot_when = _list(evidence.get("screenshot_when"), "evidence.screenshot_when")
        self.screenshot_except = _list(evidence.get("screenshot_except"), "evidence.screenshot_except")
        self.review_lenses = _list(review.get("lenses"), "review.lenses") or self.default_lenses()
        self.gate_paths = _list(raw.get("gate_paths"), "gate_paths")
        designs = raw.get("designs") or {}
        self.designs_dir = (designs.get("dir") or "docs/designs").rstrip("/")
        self.feature_pattern = designs.get("branches") or "feature/*"
        self.overview = raw.get("overview") or ""
        self.docs_allow = []
        for i, d in enumerate(_list(raw.get("docs_allow"), "docs_allow")):
            d = str(d).strip().rstrip("/")
            if d.startswith("./"):
                d = d[2:]
            if not d or d in (".", "..") or d.startswith(("/", "../")) or any(c in d for c in "*?["):
                raise ConfigError(f"docs_allow[{i}] must be a repository path (no globs, not the root)")
            self.docs_allow.append(d)
        self.areas = []
        for i, a in enumerate(_list(raw.get("areas"), "areas")):
            if not isinstance(a, dict) or not a.get("doc") or not a.get("paths"):
                raise ConfigError(f"areas[{i}] needs paths and doc")
            self.areas.append({"name": a.get("name") or a["doc"], "doc": a["doc"],
                               "paths": _list(a["paths"], f"areas[{i}].paths")})
        self.checks = self._checks(raw.get("checks") or {})
        tiers = raw.get("tiers") or {}
        if not isinstance(tiers, dict) or not tiers:
            raise ConfigError("tiers must list at least one tier, low to high")
        self.tiers = list(tiers)
        self.tier = {}
        for key, t in tiers.items():
            t = t or {}
            names = _list(t.get("checks"), f"tiers.{key}.checks")
            for n in names:
                if n not in self.checks:
                    raise ConfigError(f"tiers.{key}.checks names {n!r}, which is not under checks")
            self.tier[key] = {"title": t.get("title") or key, "label": str(t.get("label") or key),
                              "checks": names, "automerge": bool(t.get("automerge", False))}
        # What a tier's change does to the product (`effect`), by position unless the tier says:
        # T1 fix, T2 addition, T3 behaviour change, T4 new concept. A three-tier file keeps its
        # meaning: its T3 (a feature with a design) covers both a change and a new concept.
        for i, key in enumerate(self.tiers):
            given = (tiers[key] or {}).get("effect")
            effect = str(given) if given else EFFECTS[min(i, len(EFFECTS) - 1)]
            if effect not in EFFECTS:
                raise ConfigError(f"tiers.{key}.effect must be one of {', '.join(EFFECTS)}")
            self.tier[key]["effect"] = effect
        if any("ai-review" in t["checks"] for t in self.tier.values()):
            if len(self.review_models) != 2 or len(set(self.review_models)) != 2:
                raise ConfigError("review.models must name two different models when a tier "
                                  "needs ai-review")
        # The first tier whose work starts from a design (`needs_start`); None when no tier says so.
        self.design_tier = next((k for k, t in tiers.items() if (t or {}).get("needs_start")), None)
        # Every tier from the first design tier up starts from a design a person approved:
        # `short` (a behaviour change) or `full` (a new concept, T4). None of them merges into the
        # default branch alone: the merger treats T4 like T3.
        first = self.tiers.index(self.design_tier) if self.design_tier else len(self.tiers)
        for i, key in enumerate(self.tiers):
            given = (tiers[key] or {}).get("design")
            if given and i < first:
                raise ConfigError(f"tiers.{key}.design: only a tier from the first one with "
                                  "needs_start up starts from a design")
            if given and given not in DESIGNS:
                raise ConfigError(f"tiers.{key}.design must be short or full")
            design = ("" if i < first else
                      str(given or ("full" if self.tier[key]["effect"] == "concept" else "short")))
            self.tier[key]["design"] = design
            if design and self.tier[key]["automerge"]:
                raise ConfigError(f"tiers.{key}: a tier that starts from a design is merged into "
                                  "the default branch by a person (automerge: false)")
        self.label_to_tier = {t["label"]: k for k, t in self.tier.items()}
        if len(self.label_to_tier) != len(self.tier):
            raise ConfigError("two tiers share one label")
        unsure = (raw.get("triage") or {}).get("when_unsure", "higher")
        if unsure not in ("higher", "ask"):
            raise ConfigError("triage.when_unsure must be higher or ask")
        for lens in self.review_lenses:
            if lens not in LENSES:
                raise ConfigError(f"review.lenses: unknown lens {lens!r} ({', '.join(LENSES)})")
        scope = raw.get("scope") or {}
        if not isinstance(scope, dict):
            raise ConfigError("scope must be a mapping")
        self.scope_mode = scope.get("mode") or "full"
        if self.scope_mode not in ("own", "full"):
            raise ConfigError("scope.mode must be own or full")
        self.scope_label = str(scope.get("label") or "factory").strip()
        if not self.scope_label:
            raise ConfigError("scope.label must name a label")
        self.adopt_wait_hours = _number(scope.get("adopt_wait_hours", 24), "scope.adopt_wait_hours")
        self.adopt_per_day = _number(scope.get("adopt_per_day", 5), "scope.adopt_per_day", whole=True)

    def default_lenses(self) -> list[str]:
        product = "ux" if self.screenshot_when else "public_api"
        return ["correctness", "security", "first_principles", "tests_spec", product]

    def _checks(self, given: dict) -> dict[str, list[CheckSpec]]:
        if not isinstance(given, dict):
            raise ConfigError("checks must map a name to {job: ...}")
        gh = self.forge_kind == "github"
        out: dict[str, list[CheckSpec]] = {}
        for name, spec in given.items():
            if isinstance(spec, str):
                spec = {"job": spec}
            if not isinstance(spec, dict) or not spec.get("job"):
                raise ConfigError(f"checks.{name} needs job")
            workflow = spec.get("workflow") or ""
            app = spec.get("app") or (DEFAULT_APP if gh else "")
            if gh and app == DEFAULT_APP and not workflow:
                raise ConfigError(f"checks.{name}: on GitHub a GitHub Actions check names its "
                                  "workflow file (workflow: .github/workflows/<file>.yml)")
            out[name] = [CheckSpec(name, spec["job"], workflow if gh else "", app if gh else "")]
        wf, app = (FACTORY_WORKFLOW, DEFAULT_APP) if gh else ("", "")
        out.setdefault("ai-review", [CheckSpec("ai-review", review_job(m), wf, app, True)
                                     for m in self.review_models])
        out.setdefault("factory-docs", [CheckSpec("factory-docs", "factory-docs", wf, app)])
        return out

    # -------- queries

    def required(self, tier: str) -> list[CheckSpec]:
        seen, out = set(), []
        for name in self.tier[tier]["checks"]:
            for spec in self.checks[name]:
                if spec not in seen:
                    seen.add(spec)
                    out.append(spec)
        return out

    def all_jobs(self) -> set[str]:
        return {s.job for specs in self.checks.values() for s in specs}

    def is_feature(self, branch: str) -> bool:
        return branch != self.default_branch and match_any(branch, [self.feature_pattern])


def parse_config(text: str) -> Config:
    return Config(load_yaml(text))


def feature_config(default: Config, target: Config | None) -> Config:
    """The rules for an MR into a feature branch: the branch's factory.yml can be STRICTER, never looser.

    humans, forge, designs and the tier list come from the default branch only, because a
    feature branch can be pushed by the crew without review. From the target branch come extra
    required checks per tier, extra gate paths and extra screenshot globs; automerge holds only
    when both say so.
    """
    if target is None:
        return default
    merged = Config(default.raw)
    for name, specs in target.checks.items():
        merged.checks.setdefault(name, specs)
    for key, t in merged.tier.items():
        other = target.tier.get(key)
        if other:
            t["checks"] = t["checks"] + [c for c in other["checks"] if c not in t["checks"]]
            t["automerge"] = t["automerge"] and other["automerge"]
    merged.gate_paths = default.gate_paths + [g for g in target.gate_paths if g not in default.gate_paths]
    merged.screenshot_when = default.screenshot_when + [
        g for g in target.screenshot_when if g not in default.screenshot_when]
    merged.areas = target.areas or default.areas
    return merged


# ---------------------------------------------------------------- globs


_GLOB_CACHE: dict[str, re.Pattern] = {}


def _brace_expand(p: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", p)
    if not m:
        return [p]
    return [y for x in m.group(1).split(",") for y in _brace_expand(p[: m.start()] + x + p[m.end():])]


def _glob_body(p: str) -> str:
    i, out = 0, ""
    while i < len(p):
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    return out


def glob_re(pattern: str) -> re.Pattern:
    """Repository-root glob: `**` spans directories (also none), `*` and `?` stay in one segment,
    `{a,b}` is either alternative (as in an AGENTS.md area table: `node/{cli,options}.ts`)."""
    if pattern in _GLOB_CACHE:
        return _GLOB_CACHE[pattern]
    out = "(?:" + "|".join(_glob_body(x) for x in _brace_expand(pattern.lstrip("/"))) + ")"
    rx = re.compile(out + r"\Z")
    _GLOB_CACHE[pattern] = rx
    return rx


def match_any(path: str, patterns: list[str]) -> bool:
    return any(glob_re(p).match(path) for p in patterns)


# ---------------------------------------------------------------- forge interface


# Plain NamedTuples rather than dataclasses: the cron host may load the one-file merger without
# registering it in sys.modules, and dataclasses need that.


class MR(NamedTuple):
    number: int
    title: str
    body: str
    source: str
    target: str
    head: str
    draft: bool = False
    labels: tuple = ()
    author: str = ""
    mergeable: str = "ok"         # ok | checking | conflict | behind
    same_repo: bool = True


class Check(NamedTuple):
    name: str
    status: str                   # success | failed | pending | skipped | manual | cancelled | neutral
    workflow: str = ""
    app: str = ""
    allow_failure: bool = False
    id: int = 0
    started: str = ""             # ISO time the run was created or started; "" unknown


class Comment(NamedTuple):
    author: str
    body: str
    created_at: str = ""          # ISO time; "" unknown


class Issue(NamedTuple):
    number: int
    title: str
    body: str
    author: str
    open: bool = True
    labels: tuple = ()


class ForgeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Forge:
    """What the rules need from a forge. `forges.GitLab` and `forges.GitHub` implement it."""

    kind = ""
    mr_ref = "!"                   # how a merge request is referenced in text: !12 or #12

    def default_branch(self) -> str: raise NotImplementedError
    def read_file(self, path: str, ref: str) -> str | None: raise NotImplementedError
    def blob_id(self, path: str, ref: str) -> str | None: raise NotImplementedError  # None: no such file
    def branch_exists(self, branch: str) -> bool: raise NotImplementedError
    def open_mrs(self) -> list[MR]: raise NotImplementedError          # oldest first
    def get_mr(self, number: int) -> MR: raise NotImplementedError
    def checks(self, mr: MR) -> list[Check] | None: raise NotImplementedError  # None: nothing ran on head yet
    def changed_paths(self, mr: MR) -> list[str]: raise NotImplementedError
    # Earlier heads (never mr.head) on which one of `jobs` failed: the blocked review rounds.
    def blocked_review_heads(self, mr: MR, jobs: set[str]) -> set[str]: raise NotImplementedError
    def open_threads(self, mr: MR) -> int: raise NotImplementedError
    def comments(self, mr: MR) -> list[Comment]: raise NotImplementedError
    def approvers(self, mr: MR) -> set[str]: raise NotImplementedError     # approvals valid for mr.head
    def closing_issues(self, mr: MR) -> list[int]: raise NotImplementedError
    def label_events(self, issue: int) -> list[dict]: raise NotImplementedError
    def mr_label_events(self, mr: MR) -> list[dict]: raise NotImplementedError  # as label_events
    def issue_is_open(self, issue: int) -> bool: raise NotImplementedError
    def issue(self, number: int) -> Issue: raise NotImplementedError
    def issue_comments(self, issue: int) -> list[Comment]: raise NotImplementedError  # oldest first
    def merge(self, mr: MR) -> None: raise NotImplementedError             # at mr.head only
    # For the merger's report only; neither decides anything. The web address of the merge
    # request, "" unknown; and what the forge says of a merge just made:
    # {"merge_commit": sha|None, "by": login|None, "at": ISO time|None}.
    def mr_url(self, mr: MR) -> str: return ""
    def merged_info(self, mr: MR) -> dict: return {}
    def rebase(self, mr: MR) -> None: raise NotImplementedError
    def rebase_in_progress(self, mr: MR) -> bool: raise NotImplementedError
    # A new pipeline on an up-to-date head for an adopted merge request: update the branch when
    # it is behind its target, else start the head's pipeline again. Returns what it did.
    def refresh(self, mr: MR) -> str: raise NotImplementedError
    def comment(self, mr: MR, body: str) -> None: raise NotImplementedError
    def comment_issue(self, issue: int, body: str) -> None: raise NotImplementedError
    def close_issue(self, issue: int) -> None: raise NotImplementedError
    def remove_issue_label(self, issue: int, label: str) -> None: raise NotImplementedError
    # The default branch's latest result: (green | red | pending | unknown, failing job names,
    # where: a pipeline or commit reference for people).
    def branch_status(self, branch: str) -> tuple[str, list[str], str]: raise NotImplementedError
    def find_issues(self, label: str) -> list[int]: raise NotImplementedError   # open, with the label
    def create_issue(self, title: str, body: str, labels: list[str]) -> int: raise NotImplementedError
    def update_issue(self, issue: int, body: str) -> None: raise NotImplementedError
    # The anchors of the blocking findings a review job reported for `head` (its
    # `[FACTORY-ANCHORS]` line); {"none"} without blocking findings, None when unreadable.
    def review_anchors(self, mr: MR, check: Check) -> set[str] | None: raise NotImplementedError


# ---------------------------------------------------------------- tiers


TIER_IGNORED = "neither in humans nor the triager: ignored"


def issue_tier(events: list[dict], cfg: Config) -> tuple[str, str]:
    """The tier an issue counts as: tiers only go up, unless a person in `humans` lowers one.

    `events` are {created_at, action: add|remove, label, user}, replayed oldest first.
    - The first tier counts when a person in `humans` or the `triager` added it, or when it is
      the highest. A first tier below the highest from anyone else does not count: the issue
      stays at the highest tier, and the why says so (`TIER_IGNORED`).
    - After that, a higher tier counts from anyone (an up-tier: the crew moves an issue up when
      it finds the change does more than its tier allows; a higher tier is only ever stricter).
    - A lower tier counts only from a person in `humans`; from anyone else, the crew and the
      triager included, it is ignored and the issue keeps the tier it had.
    - Removing a label never lowers the tier, and a higher label still on holds. No tier label
      left at all: the highest tier.
    """
    tiers = cfg.tiers
    top = len(tiers) - 1
    current, ignored, active = None, None, set()
    for ev in sorted(events, key=lambda e: e.get("created_at") or ""):
        key = cfg.label_to_tier.get(ev.get("label") or "")
        if key is None:
            continue
        rank = tiers.index(key)
        if ev.get("action") == "remove":
            active.discard(rank)
            continue
        if ev.get("action") != "add":
            continue
        active.add(rank)
        user = ev.get("user") or ""
        if user in cfg.humans:
            current = rank
        elif current is None:
            if user in cfg.triager or rank == top:
                current = rank
            else:
                ignored = (rank, user)
        elif rank > current:
            current = rank
    if not active:
        return tiers[-1], "has no tier label"
    if current is None:
        rank, user = ignored if ignored else (max(active), "")
        return tiers[-1], f"has {tiers[rank]} from `{user or 'unknown'}`, {TIER_IGNORED}, so {tiers[-1]}"
    if max(active) < current:
        return tiers[current], f"had {tiers[current]}; a lower tier counts only from a person"
    # A person who added a lower tier but left the higher label on: the higher one still holds.
    current = max(current, max(active))
    return tiers[current], f"is {tiers[current]}"


TRIAGE_MARK = "<!-- factory-triage -->"
# The triager's citation of the documented behaviour a T1 (a fix) restores, one line of its
# comment: Spec: `docs/areas/x.md:42` "the line, quoted". No such line: the issue is not a fix.
# These patterns run over text anyone can write (a description, a comment), so every one is
# linear in its input: a space, a tab or a non-breaking space (`_W`), never `\s`, between the
# parts of a line (under `(?m)` a `\s*` after `^` walks every blank line that follows, at each
# of them), no two neighbouring runs that can share the same spaces, and no tail that can fail
# after a part that can end in many places. `\r?$` keeps a line that ends in CRLF.
_W = r"[ \t\xa0]"
SPEC_LINE = re.compile(r'(?m)^' + _W + r'*Spec:' + _W + r'*`?([A-Za-z0-9_./@+-]+):(\d+)`?' + _W
                       + r'*(?:[-\u2014:]' + _W + r'*)?["\u201c]([^\r\n]+?)["\u201d]' + _W + r'*\r?$')


class Citation(NamedTuple):
    path: str
    line: int
    quote: str
    author: str


def triage_citation(comments: list[Comment], cfg: Config) -> Citation | None:
    """The spec line the latest triage comment cites, or None.

    Only a comment carrying TRIAGE_MARK from the `triager` or a person in `humans` counts:
    anyone can comment on an issue, so the author is what makes it the triage decision. A path
    that leaves the repository (absolute, `..`) is no citation.
    """
    for c in reversed(comments):
        if TRIAGE_MARK not in c.body or not (c.author in cfg.triager or c.author in cfg.humans):
            continue
        m = SPEC_LINE.search(c.body)
        if not m:
            return None
        path = m.group(1)
        if path.startswith("/") or ".." in path.split("/"):
            return None
        return Citation(path, int(m.group(2)), m.group(3).strip(), c.author)
    return None


# A closing line: the keyword at the start of a line, then the issue, then whatever the author
# went on with (`Closes #35. Step 1 of the design: ...`, as a crew writes it). FIXES finds the
# line and gives what follows the keyword; FIXES_REF reads the references at its start.
FIXES = re.compile(r"(?im)^" + _W + r"*(?:fix(?:es|ed)?|close[sd]?|resolve[sd]?)(?:" + _W + r"*:)?" + _W
                   + r"+(#\d[^\r\n]*)")
# One reference, after the first one behind `,`, `&` or `and`. `#12abc` and `#1#2` are none.
FIXES_REF = re.compile(r"(?i)" + _W + r"*(?:(?:,|&|and\b)" + _W + r"*)?#(\d+)(?![\w#])")


def fixes_issues(body: str) -> list[int]:
    """Issue numbers from the closing lines of a description, the convention for every MR.

    A closing line starts with `Fixes`, `Closes` or `Resolves` (any form, an optional colon)
    and names its issues right after it: `Fixes #12`, `Closes #35. Step 1 of ...`,
    `Resolves #3, #4 and #5: ...`. Text after the references is not read. The keyword anywhere
    else in a line (`this closes #3`), a quoted line or a list item is no closing line.
    """
    found: set[int] = set()
    for tail in FIXES.findall(body or ""):
        pos, first = 0, True
        while True:
            m = FIXES_REF.match(tail, pos)
            # The first reference follows the keyword directly, with no separator before it.
            if not m or (first and m.start(1) != 1):
                break
            found.add(int(m.group(1)))
            pos, first = m.end(), False
    return sorted(found)


def mr_tier(forge: Forge, mr: MR, cfg: Config) -> tuple[str, str]:
    # Only open issues count: a merge request citing an already closed (perhaps lower-tier)
    # issue must not borrow its tier.
    closes = [n for n in forge.closing_issues(mr) if forge.issue_is_open(n)]
    if not closes:
        return cfg.tiers[-1], "closes no open issue"
    found = []
    for n in closes:
        tier, why = issue_tier(forge.label_events(n), cfg)
        found.append((tier, f"#{n} {why}"))
    return max(found, key=lambda t: cfg.tiers.index(t[0]))


# ---------------------------------------------------------------- scope


def ts(value) -> datetime.datetime | None:
    """An ISO-8601 forge timestamp as an aware datetime, or None."""
    try:
        t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def label_adder(events: list[dict], label: str) -> str | None:
    """Who added `label` last, when that add was not removed since; None otherwise."""
    who = None
    for ev in sorted(events, key=lambda e: e.get("created_at") or ""):
        if ev.get("label") != label:
            continue
        if ev.get("action") == "add":
            who = ev.get("user") or ""
        elif ev.get("action") == "remove":
            who = None
    return who


def scope_labels(forge: Forge, mr: MR, cfg: Config) -> tuple[bool, list[int], list[str]]:
    """(the merge request's own `scope.label` counts, the closed issues whose label counts, ignored).

    The label counts only when a person in `humans` added it: anyone who may edit labels could
    otherwise bring a merge request into the factory, and a T1 into an automatic merge.
    `ignored` names each label that is on but was added by someone else, for the merger to say.
    """
    ignored = []
    own = False
    if cfg.scope_label in mr.labels:
        who = label_adder(forge.mr_label_events(mr), cfg.scope_label)
        own = who in cfg.humans
        if not own:
            ignored.append(f"on {forge.mr_ref}{mr.number} by `{who or 'unknown'}`")
    issues = []
    for n in forge.closing_issues(mr):
        if cfg.scope_label not in forge.issue(n).labels:
            continue
        who = label_adder(forge.label_events(n), cfg.scope_label)
        if who in cfg.humans:
            issues.append(n)
        else:
            ignored.append(f"on #{n} by `{who or 'unknown'}`")
    return own, issues, ignored


def labelled_issues(forge: Forge, mr: MR, cfg: Config) -> list[int]:
    """The issues the merge request closes whose `scope.label` a person in `humans` added."""
    return scope_labels(forge, mr, cfg)[1]


def in_scope(forge: Forge, mr: MR, cfg: Config) -> tuple[bool, str]:
    """(whether the factory acts on this merge request, why): `scope_decision` without `ignored`."""
    return scope_decision(forge, mr, cfg)[:2]


def scope_decision(forge: Forge, mr: MR, cfg: Config) -> tuple[bool, str, list[str]]:
    """(whether the factory acts on this merge request, why, ignored labels). `cfg` is the default branch's.

    Full mode: every merge request. Own mode: a merge request into a feature branch (those are
    the factory's own), or into the default branch when it carries `scope.label` or closes an
    issue that carries it, each added by a person in `humans` (`scope_labels`). Anything else
    is the team's, and the factory leaves it alone.
    """
    if cfg.scope_mode == "full":
        return True, "full mode", []
    if cfg.is_feature(mr.target):
        return True, f"into feature branch {mr.target}", []
    if mr.target != cfg.default_branch:
        return False, f"into {mr.target}, not {cfg.default_branch} or a feature branch", []
    own, issues, ignored = scope_labels(forge, mr, cfg)
    if own:
        return True, f"labelled {cfg.scope_label!r} by a person", ignored
    if issues:
        return True, f"closes #{issues[0]}, labelled {cfg.scope_label!r} by a person", ignored
    if ignored:
        return False, (f"{cfg.scope_label!r} not added by a person in humans "
                       f"({', '.join(ignored)}): ignored"), ignored
    return False, f"not labelled {cfg.scope_label!r} and closes no issue labelled so", []


def is_crew_author(forge: Forge, mr: MR, cfg: Config) -> bool:
    """Whether the merge request's author speaks as the crew (may post `false-positive`)."""
    if not mr.author or mr.author in cfg.humans:
        return False
    if cfg.crew:
        return mr.author in cfg.crew
    return not is_adopted(forge, mr, cfg)


def is_adopted(forge: Forge, mr: MR, cfg: Config) -> bool:
    """An old merge request a person brought into the factory: own mode, into the default branch,
    in scope only through its own `scope.label` (it closes no issue that carries the label);
    both count only as `scope_labels` reads them.

    The crew's merge requests always close the labelled issue they were started from, so they
    are never adopted; a person's merge request is adopted the moment a person labels it.
    """
    if not (cfg.scope_mode == "own" and mr.target == cfg.default_branch and cfg.scope_label in mr.labels):
        return False
    own, issues, _ = scope_labels(forge, mr, cfg)
    return own and not issues


# ---------------------------------------------------------------- the merge check

WAIT = "wait"
STALE = "stale"     # an adopted merge request's checks ran before its adoption: not yet judged


class NeedsApproval(str):
    """A refusal of `check_mr` that a person's approval alone lifts: every other rule held.
    The text is the refusal as before; the type is what the merger's report reads."""


# `/factory override ai-review <sha> <class>[ #N | <url>][ model=<model>]: <reason>`. The full
# 40-character head sha, compared for equality: a short prefix could be matched by a later commit
# crafted to share it, and the override would then clear a review nobody saw.
OVERRIDE = re.compile(
    r"(?im)^" + _W + r"*/factory" + _W + r"+override" + _W + r"+ai-review" + _W + r"+([0-9a-f]{40})" + _W
    + r"+([a-z-]+)(?:" + _W + r"+(#\d+|https://\S+))?(?:" + _W + r"+model=(\S+?))?" + _W + r"*:" + _W
    # The reason runs to the end of the line and cannot fail there: the URL and the model can
    # end at each of their colons, and a tail that could fail would be tried from every one.
    + r"*(\S[^\n]*)$")
# The class names why a block is released, and what it must carry: #N an issue, <url> a job.
OVERRIDE_CLASSES = {"false-positive": "", "not-applicable": "", "owner-decision": "",
                    "pre-existing": "#", "deferred": "#", "infra-failure": "url"}
REASON_MIN, REASON_MAX = 20, 500
# The crew may release its own block only as a false positive, and never one of these: a wrong
# call there ships a hole, a crash or lost data, and only a person takes that risk. Nor the
# gates a person owns: what the product does (product-shape, a one-way design door) and the
# missing screenshot of a user-visible change (evidence-gap).
CREW_CLASS = "false-positive"
CREW_NEVER = frozenset({"security", "crash", "data-loss", "corruption", "guard-removal",
                        "product-shape", "design-one-way-door", "evidence-gap"})
# Bounded, so that a body of `![` or `<img ` repeated costs its length times the bound and not
# its length squared. An image whose alt text or tag is longer than the bound does not count.
IMAGE = re.compile(r"!\[[^\]]{0,500}\]\([ \t]{0,100}[^)\s]|<img\s[^>]{0,500}?src[ \t]{0,100}=", re.I)
_FAILED = {"failed", "cancelled"}


ANCHORS = re.compile(r"^\[FACTORY-ANCHORS\] ([0-9a-f]{40}) ([a-z,-]+)\s*$", re.M)


def last_anchors(text: str, head: str) -> set[str] | None:
    """The anchors on the LAST `[FACTORY-ANCHORS] <sha> <a,b>` line of a review job's output.

    review.sh prints that line after the transcript, so an earlier line the agent printed
    cannot stand in for it. None when the last line is for another sha or there is none.
    """
    found = ANCHORS.findall(text or "")
    if not found or found[-1][0] != head.lower():
        return None
    return set(found[-1][1].split(","))


class Override(NamedTuple):
    who: str
    cls: str
    ref: str           # #N or a URL, as the class requires; "" otherwise
    model: str         # "" clears every review job of the head
    reason: str
    crew: bool         # posted by the merge request's author, not by a person in `humans`

    def clears(self, spec: CheckSpec) -> bool:
        return spec.review and (not self.model or review_job(self.model) == spec.job)

    def text(self) -> str:
        what = f"{self.cls} {self.ref}".strip()
        return f"by {self.who} ({what}{', crew' if self.crew else ''}): {self.reason}"


def parse_overrides(comments: list[Comment], head: str, cfg: Config, author: str = "") -> list[Override]:
    """Valid overrides for `head`, oldest first.

    From a person in `humans`: any class. From the merge request's `author` (the crew): only
    `false-positive`; check_mr also checks the blocked review's anchors. The class's reference
    must be there (`#N` for pre-existing and deferred, an https job URL for infra-failure) and
    the reason must be 20-500 characters; anything else is ignored, as is a sha that is not the
    head's full sha. `model=` must name one of `review.models`.
    """
    out = []
    models = {review_job(m) for m in cfg.review_models}
    for c in comments:
        person = c.author in cfg.humans
        crew = not person and bool(author) and c.author == author
        if not (person or crew):
            continue
        for sha, cls, ref, model, reason in OVERRIDE.findall(c.body or ""):
            reason = reason.strip()
            if head.lower() != sha.lower() or cls not in OVERRIDE_CLASSES:
                continue
            need = OVERRIDE_CLASSES[cls]
            if (need == "#") != ref.startswith("#") or (need == "url") != ref.startswith("https://"):
                continue
            if not REASON_MIN <= len(reason) <= REASON_MAX:
                continue
            # A placeholder left as it was (`<reason, 20 to 500 characters>`) is no reason.
            if reason.startswith("<") and reason.endswith(">") and "<" not in reason[1:] and ">" not in reason[:-1]:
                continue
            if model and review_job(model) not in models:
                continue
            if crew and cls != CREW_CLASS:
                continue
            out.append(Override(c.author, cls, ref, model, reason, crew))
    return out


def _matches(check: Check, spec: CheckSpec, kind: str) -> bool:
    if check.name != spec.job:
        return False
    if kind == "github":
        return check.app == spec.app and (not spec.workflow or check.workflow == spec.workflow)
    return True


def new_to_default(forge: Forge, mr: MR, paths: list[str], cfg: Config) -> tuple[list[str], list[str]]:
    """(paths whose head content differs from the default branch, paths equal to it).

    A merge request into a feature branch that brings the default branch in (a sync) changes
    gate paths only to what a person already approved on the default branch; those need no new
    approval. Only a file present on both with the same content counts as equal: a file
    missing on either side (a deletion, or a head the forge cannot read, as for some forks)
    still needs a person, and so does every path when a file cannot be read.
    """
    if not mr.same_repo:
        return list(paths), []
    differ, same = [], []
    try:
        for p in paths:
            mine = forge.blob_id(p, mr.head)
            theirs = forge.blob_id(p, cfg.default_branch) if mine is not None else None
            (same if mine is not None and mine == theirs else differ).append(p)
    except (ForgeError, NotImplementedError, KeyError, TypeError):
        return list(paths), []
    return differ, same


def _release(forge: Forge, mr: MR, run: Check, spec: CheckSpec, overrides: list[Override]) -> Override | None:
    """The override that releases this failed review job, a person's before the crew's."""
    people = [o for o in overrides if not o.crew and o.clears(spec)]
    if people:
        return people[-1]
    crew = [o for o in overrides if o.crew and o.clears(spec)]
    if not crew:
        return None
    try:
        anchors = forge.review_anchors(mr, run)
    except (ForgeError, NotImplementedError, KeyError, TypeError, ValueError):
        anchors = None
    # Unreadable, no blocking finding (a lens error is no false positive), or an anchor the crew
    # never releases: a person decides.
    if not anchors or anchors == {"none"} or anchors & CREW_NEVER or anchors & {"unknown", "infra"}:
        return None
    return crew[-1]


def review_infra_failed(forge: Forge, mr: MR, run: Check) -> bool:
    """Whether this failed review job says `infra`: a lens did not run and nothing was found."""
    try:
        return forge.review_anchors(mr, run) == {"infra"}
    except (ForgeError, NotImplementedError, KeyError, TypeError, ValueError):
        return False


def check_mr(forge: Forge, mr: MR, cfg: Config, since: datetime.datetime | None = None
             ) -> tuple[str | None, str]:
    """(reason, note). reason None: mergeable now; WAIT: ask again later; else a one-line refusal.

    `cfg` is the effective config for the MR's target (feature_config for a feature branch).
    `note` says what was used (tier, override) for the merge report. `since` (an adopted merge
    request): every check the tier requires must have run after it; an older run is STALE, so a
    green pipeline from before the adoption never counts.
    """
    if mr.draft:
        return "draft", ""
    held = HOLD_LABELS & set(mr.labels)
    if held:
        return f"label {sorted(held)[0]!r} holds it for a person", ""
    checks = forge.checks(mr)
    if not checks or any(c.status == "pending" for c in checks):
        return WAIT, ""
    tier, why = mr_tier(forge, mr, cfg)
    note = f"{tier}: {why}"
    required = cfg.required(tier)
    comments = forge.comments(mr)
    # The author's `false-positive` override is the crew's. An adopted merge request's author is
    # a person outside `humans`, who must not clear a review block on their own change.
    crew_author = mr.author if is_crew_author(forge, mr, cfg) else ""
    overrides = (parse_overrides(comments, mr.head, cfg, crew_author)
                 if any(s.review for s in required) else [])
    bad, missing, overridden, used = [], [], [], []
    for spec in required:
        runs = [c for c in checks if _matches(c, spec, forge.kind)]
        if not runs:
            missing.append(spec.job)
            continue
        run = max(runs, key=lambda c: c.id)
        if since is not None:
            started = ts(run.started)
            if started is None or started < since:
                return STALE, ""
        if run.status == "success":
            continue
        if spec.review and run.status in _FAILED:
            release = _release(forge, mr, run, spec, overrides)
            if release:
                used.append(release)
                overridden.append(f"{spec.job} overridden {release.text()}")
                continue
            if review_infra_failed(forge, mr, run):
                bad.append(f"{spec.job}=review infra failure (a lens did not run, nothing found): retry the job")
                continue
        bad.append(f"{spec.job}={run.status}")
    # The factory's own jobs: the checks of every tier, and the jobs that show each review on the
    # merge request. Those decide nothing, and GitHub keeps a run that an edit of the title
    # cancelled for as long as the head stays. Exact names of the configured models only.
    known = cfg.all_jobs() | {comment_job(m) for m in cfg.review_models}
    required_jobs = {s.job for s in required}
    for c in checks:
        # Outside the tier: a failure blocks unless it is allowed to fail or is a factory check
        # that only another tier needs (the review jobs skip themselves that way).
        if c.name in required_jobs or c.status not in _FAILED:
            continue
        if c.allow_failure or c.name in known:
            continue
        bad.append(f"{c.name}={c.status}")
    if bad:
        return "check not green: " + ", ".join(sorted(set(bad))) + f" ({note})", note
    if missing:
        return "required check did not run: " + ", ".join(sorted(set(missing))) + f" ({note})", note
    if overridden:
        note += "; " + "; ".join(overridden)
    for ref in sorted({o.ref for o in used if o.cls == "deferred"}):
        if not forge.issue_is_open(int(ref[1:])):
            return (f"an override defers to {ref}, which is not an open issue: open it, or "
                    "override with another class"), note
    threads = forge.open_threads(mr)
    if threads:
        return f"{threads} review thread(s) unresolved", note
    paths = forge.changed_paths(mr)
    if cfg.screenshot_when:
        ui = sorted({p for p in paths if match_any(p, cfg.screenshot_when)
                     and not match_any(p, cfg.screenshot_except)})
        if ui and not any(IMAGE.search(t) for t in [mr.body] + [c.body for c in comments]):
            return ("changes what users see (" + ", ".join(ui[:3]) + ") but no screenshot is "
                    "attached: add an after screenshot (and a before one if the screen existed)"), note
    approved = None

    def human_approved() -> bool:
        nonlocal approved
        if approved is None:
            approved = bool(forge.approvers(mr) & cfg.humans)
        return approved

    hits = sorted({p for p in paths if match_any(p, cfg.gate_paths)})
    if hits and cfg.is_feature(mr.target):
        hits, synced = new_to_default(forge, mr, hits, cfg)
        if synced:
            note += f"; {len(synced)} gate path(s) already as on {cfg.default_branch}"
    word = "merge request" if forge.kind == "gitlab" else "pull request"
    if hits and not human_approved():
        return NeedsApproval("touches " + ", ".join(hits[:4]) + f": needs a person's approval on the {word}"), note
    to_default = mr.target == cfg.default_branch
    if to_default and cfg.is_feature(mr.source) and not human_approved():
        return NeedsApproval(f"merges feature branch {mr.source} into {mr.target}: only a person ships a whole "
                             f"feature, approve the {word}"), note
    if to_default and not cfg.tier[tier]["automerge"] and not human_approved():
        return NeedsApproval(f"{tier} ({cfg.tier[tier]['title']}) is not merged alone: a person approves the "
                             f"{word} ({why}); every other rule holds, so it would merge on that approval"), note
    return None, note
