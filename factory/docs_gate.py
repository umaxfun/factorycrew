#!/usr/bin/env python3
"""factory-docs: a deterministic check that the docs an agent starts from still match the repo.

Usage: python3 factory/docs_gate.py [--root .] [--config factory.yml]
                                    [--baseline factory/docs-baseline.txt] [--write-baseline]

Docs checked: AGENTS.md, factory.yml `overview`, and every `areas[].doc`. Findings, one line each:

  LINK         a relative link or #anchor that does not resolve
  PATH         a backticked repository path that does not exist (of a test node id
               `tests/test_x.py::test_y`, the file before `::`)
  STALE-LINE   `path:N` or `path:N-M` past the end of the file
  PHANTOM-CODE a backticked call `name()` / `Type.name()` that appears in no tracked code file
  UNREACHABLE  an area doc or the overview that no link chain from AGENTS.md reaches
  MISSING-DOC  an areas[].doc or the overview that does not exist
  PHANTOM-AREA an areas[].paths glob that matches no tracked file
  AREA-MAP     the AGENTS.md area table and factory.yml `areas` differ (never baselined)

Paths in factory.yml `docs_allow` (and `--allow`) exist only after a build or a test run (a
report folder, generated bindings): a PATH under one of them is not a finding. Use it for build
outputs instead of the baseline, so the baseline holds only real debt.

The baseline lists findings accepted at assessment (debt), one per line, exactly as printed.
A baselined finding is reported but does not fail; a baseline line that no longer occurs is
reported as fixed so the baseline can shrink. Exit 1 on any finding not in the baseline.

A brace pattern `a/{b,c}.py` is checked as each path it stands for. One over the limits (256
paths, 64 brace groups, 1024 characters) is checked as written, and an `areas[].paths` glob
over them is an AREA-MAP finding.

The link and path rules are the repo-readiness skill's docs-check.py, copied here because a
repository vendors factory/ only. Standard library plus PyYAML (through fcore).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fcore import ConfigError, glob_re, parse_config  # noqa: E402

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb",
            ".php", ".cs", ".c", ".h", ".cc", ".cpp", ".hpp", ".swift", ".scala", ".vue", ".svelte",
            ".sh", ".lua", ".ex", ".exs", ".dart", ".m", ".sql"}
CALL = re.compile(r"^([A-Za-z_][\w]*(?:\.[A-Za-z_]\w*)*)\(\)$")


# ---------------------------------------------------------------- docs-check (copied)

def slug(heading: str) -> str:
    h = re.sub(r"[`*_]", "", heading.strip().lower())
    h = re.sub(r"[^\w\- ]", "", h)
    return h.replace(" ", "-")


# One `{a,b}` group, innermost first: the group fcore.glob_re expands, so a brace pattern means
# the same paths in a doc, in the AGENTS.md table and in a factory.yml glob.
BRACES = re.compile(r"\{([^{}]*)\}")
MAX_VARIANTS = 256  # the paths one brace pattern may stand for
MAX_GROUPS = 64     # the `{` in one pattern
MAX_LENGTH = 1024   # its length; no path is longer. The three keep the work bounded.


def brace_variants(p: str) -> list[str] | None:
    """The paths a brace pattern stands for (`a/{b,c}.py`: `a/b.py`, `a/c.py`), or None when
    there are more than MAX_VARIANTS of them, or the pattern is longer than MAX_LENGTH or has
    more than MAX_GROUPS groups.

    Thirty `{a,b}` groups stand for 2**30 paths: the count is checked before a group is
    expanded, so the work is bounded whatever the text is."""
    if len(p) > MAX_LENGTH or p.count("{") > MAX_GROUPS:
        return None
    done, todo = [], [p]
    while todo:
        q = todo.pop()
        m = BRACES.search(q)
        if not m:
            done.append(q)
            continue
        alts = m.group(1).split(",")
        if len(done) + len(todo) + len(alts) > MAX_VARIANTS:
            return None
        todo.extend(q[: m.start()] + x + q[m.end():] for x in reversed(alts))
    return done


def expand(p: str) -> list[str]:
    """The paths `p` stands for, each once; `p` as written when brace_variants gives up on it."""
    return list(dict.fromkeys(brace_variants(p) or [p]))


def headings(path: str) -> set[str]:
    with open(path, encoding="utf-8") as fh:
        return {slug(h) for h in re.findall(r"^#+ (.*)$", fh.read(), re.M)}


def strip_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def links(text: str) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", strip_fences(text))


# ---------------------------------------------------------------- repository


def tracked_files(root: str) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", root, "ls-files", "-z"], capture_output=True, check=True)
        files = [f for f in out.stdout.decode("utf-8", "replace").split("\0") if f]
        if files:
            return files
    except (OSError, subprocess.CalledProcessError):
        pass
    found = []
    for d, dirs, names in os.walk(root):
        dirs[:] = [x for x in dirs if x not in (".git", "node_modules")]
        found += [os.path.relpath(os.path.join(d, n), root) for n in names]
    return found


class Repo:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.files = tracked_files(self.root)
        self.top = set(os.listdir(self.root))
        self._code = None

    def code_text(self) -> str:
        if self._code is None:
            parts = []
            for f in self.files:
                if os.path.splitext(f)[1] in CODE_EXT:
                    try:
                        with open(os.path.join(self.root, f), encoding="utf-8", errors="replace") as fh:
                            parts.append(fh.read())
                    except OSError:
                        continue
            self._code = "\n".join(parts)
        return self._code

    def line_count(self, rel: str) -> int:
        with open(os.path.join(self.root, rel), encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)


def check_doc(repo: Repo, rel: str, allow: set[str]) -> list[str]:
    """LINK, PATH, STALE-LINE and PHANTOM-CODE findings of one doc."""
    path = os.path.join(repo.root, rel)
    with open(path, encoding="utf-8") as fh:
        body = strip_fences(fh.read())
    base = os.path.dirname(path)
    out = []
    for target in links(body):
        if re.match(r"^(https?:|mailto:)", target):
            continue
        p, _, anchor = target.partition("#")
        tgt = os.path.normpath(os.path.join(base, p)) if p else path
        ok = os.path.exists(tgt)
        if ok and anchor and tgt.endswith(".md"):
            ok = anchor.lower() in headings(tgt)
        if not ok:
            out.append(f"LINK {rel}: {target}")
    for token in re.findall(r"`([^`\s]+)`", body):
        m = CALL.match(token)
        if m:
            name = m.group(1).split(".")[-1]
            if not re.search(rf"\b{re.escape(name)}\b", repo.code_text()):
                out.append(f"PHANTOM-CODE {rel}: {token}")
            continue
        # A test node id (`tests/test_x.py::test_y[param]`) names a file, then what is in it:
        # the file is the path to check.
        ref = token.split("::", 1)[0]
        lines = re.search(r":([0-9]+)(?:-([0-9]+))?$", ref)
        p = re.sub(r":[0-9][0-9,\-]*$", "", ref).rstrip("/")
        if p.startswith("./"):
            p = p[2:]
        if p.split("/", 1)[0] not in repo.top:
            continue
        for q in expand(p):
            if any(c in q for c in "*<>$"):
                continue
            full = os.path.join(repo.root, q)
            if q in allow or any(q.startswith(a + "/") for a in allow):
                continue
            if not os.path.exists(full):
                out.append(f"PATH {rel}: {token}")
                continue
            if lines and os.path.isfile(full):
                last = int(lines.group(2) or lines.group(1))
                if last > repo.line_count(q):
                    out.append(f"STALE-LINE {rel}: {token}")
    return out


def reachable(repo: Repo, start: str) -> set[str]:
    """Docs reached from `start` by relative markdown links, transitively."""
    seen, todo = set(), [start]
    while todo:
        rel = todo.pop()
        if rel in seen or not os.path.isfile(os.path.join(repo.root, rel)):
            continue
        seen.add(rel)
        if not rel.endswith(".md"):
            continue
        with open(os.path.join(repo.root, rel), encoding="utf-8") as fh:
            text = fh.read()
        for t in links(text):
            p = t.partition("#")[0]
            if not p or re.match(r"^(https?:|mailto:)", p):
                continue
            todo.append(os.path.normpath(os.path.join(os.path.dirname(rel), p)))
        for t in re.findall(r"`([^`\s]+\.md)`", strip_fences(text)):
            todo.append(os.path.normpath(_dot(t)))
    return seen


# ---------------------------------------------------------------- the area map


def _dot(p: str) -> str:
    return p[2:] if p.startswith("./") else p


def _norm_path(p: str) -> str:
    p = _dot(p.strip())
    while p.endswith("/**") or p.endswith("/"):
        p = p[:-3] if p.endswith("/**") else p[:-1]
    return p


def agents_area_table(text: str, overview: str | None = None) -> dict[str, set[str]]:
    """{doc: {paths}} from the markdown table(s) in AGENTS.md that map paths to area docs.

    A row names its doc by a markdown link, or else by a backticked `.md` path in its LAST
    cell. Rows whose doc is the overview, and rows with no doc ("project files, no area"),
    are not areas. Backticked names after "except" in a cell are exclusions, not paths:
    `bookmarks/api/` (except `auth.py`) maps `bookmarks/api` only. Braces are expanded on
    both sides, so `node/{cli,options}.ts` equals `node/cli.ts` plus `node/options.ts`.
    """
    out: dict[str, set[str]] = {}
    skip = os.path.normpath(_dot(overview)) if overview else None
    for line in strip_fences(text).splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")) or re.match(r"^\|[\s:|-]+\|$", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        docs = [t.partition("#")[0] for t in re.findall(r"\]\(([^)\s]+\.md(?:#[^)\s]*)?)\)", s)]
        if not docs and cells:
            docs = [t for t in re.findall(r"`([^`]+)`", cells[-1]) if t.endswith(".md")][-1:]
        if not docs:
            continue
        doc = os.path.normpath(_dot(docs[0]))
        if doc == skip:
            continue
        ticks = []
        for c in cells:
            ticks += re.findall(r"`([^`]+)`", re.split(r"\bexcept\b", c, maxsplit=1)[0])
        paths = {_norm_path(x) for t in ticks if os.path.normpath(t) != doc for x in expand(t)}
        if paths:
            out.setdefault(doc, set()).update(paths)
    return out


def area_map_diff(agents: dict[str, set[str]], areas: list[dict]) -> list[str]:
    yml = {}
    for a in areas:
        yml.setdefault(os.path.normpath(a["doc"]), set()).update(
            _norm_path(x) for p in a["paths"] for x in expand(p))
    out = []
    for doc in sorted(set(agents) | set(yml)):
        if doc not in agents:
            out.append(f"AREA-MAP AGENTS.md: {doc} is in factory.yml areas but not in the AGENTS.md table")
        elif doc not in yml:
            out.append(f"AREA-MAP factory.yml: {doc} is in the AGENTS.md table but not in areas")
        elif agents[doc] != yml[doc]:
            out.append(f"AREA-MAP {doc}: AGENTS.md has {sorted(agents[doc])}, factory.yml has {sorted(yml[doc])}")
    return out


# ---------------------------------------------------------------- main


def run(root: str, config_text: str, baseline: list[str], allow: set[str] | None = None) -> tuple[list[str], list[str]]:
    """(findings, map_findings). map findings are never baselined."""
    cfg = parse_config(config_text)
    repo = Repo(root)
    allow = set(allow or ()) | set(cfg.docs_allow)
    findings, docs = [], ["AGENTS.md"]
    if cfg.overview:
        docs.append(cfg.overview)
    docs += [a["doc"] for a in cfg.areas]
    seen = set()
    for d in docs:
        d = os.path.normpath(d)
        if d in seen:
            continue
        seen.add(d)
        if not os.path.isfile(os.path.join(repo.root, d)):
            findings.append(f"MISSING-DOC {d}: does not exist")
            continue
        findings += check_doc(repo, d, allow)
    reach = reachable(repo, "AGENTS.md")
    for d in sorted(seen - {"AGENTS.md"}):
        if os.path.isfile(os.path.join(repo.root, d)) and d not in reach:
            findings.append(f"UNREACHABLE {d}: no link chain from AGENTS.md")
    over = []
    for a in cfg.areas:
        for g in a["paths"]:
            if brace_variants(g.lstrip("/")) is None:
                # glob_re would write out every alternative, however many: such a glob is not
                # compiled here. It is reported with the area map, so it cannot be baselined:
                # the other readers of factory.yml would still expand it.
                over.append(f"AREA-MAP factory.yml: the glob {g[:80]}{'...' if len(g) > 80 else ''} of {a['doc']} is over "
                            f"the limits ({MAX_LENGTH} characters, {MAX_GROUPS} brace groups, {MAX_VARIANTS} alternatives)")
                continue
            rx = glob_re(g)
            if not any(rx.match(f) or f.startswith(_norm_path(g) + "/") for f in repo.files):
                findings.append(f"PHANTOM-AREA {a['doc']}: {g} matches no tracked file")
    map_findings = over
    agents_path = os.path.join(repo.root, "AGENTS.md")
    if cfg.areas:
        if not os.path.isfile(agents_path):
            map_findings.append("AREA-MAP AGENTS.md: missing, factory.yml lists areas")
        else:
            with open(agents_path, encoding="utf-8") as fh:
                table = agents_area_table(fh.read(), cfg.overview)
            if not table:
                map_findings.append("AREA-MAP AGENTS.md: no table mapping paths to area docs")
            else:
                map_findings += area_map_diff(table, cfg.areas)
    return findings, map_findings


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--root", default=".")
    ap.add_argument("--config", default="factory.yml")
    ap.add_argument("--baseline", default="factory/docs-baseline.txt")
    ap.add_argument("--allow", nargs="*", default=[], help="paths that exist only after a build")
    ap.add_argument("--write-baseline", action="store_true")
    a = ap.parse_args(argv[1:])
    if os.environ.get("FACTORY_IN_SCOPE") == "0":
        # scope.mode own: factory-tier found the merge request outside the factory's stream.
        # 3 is the CI job's allowed failure (GitLab); GitHub skips the job before it runs.
        print("factory-docs: out of the factory's scope (scope.mode own): skipped")
        return 3
    cfg_path = a.config if os.path.isabs(a.config) else os.path.join(a.root, a.config)
    try:
        text = open(cfg_path, encoding="utf-8").read()
    except OSError as e:
        print(f"factory-docs: cannot read {cfg_path}: {e}")
        return 1
    base_path = a.baseline if os.path.isabs(a.baseline) else os.path.join(a.root, a.baseline)
    baseline = []
    if os.path.exists(base_path):
        with open(base_path, encoding="utf-8") as fh:
            baseline = [ln.rstrip("\n") for ln in fh if ln.strip() and not ln.startswith("#")]
    try:
        findings, map_findings = run(a.root, text, baseline, {x.rstrip("/") for x in a.allow})
    except ConfigError as e:
        print(f"factory-docs: {e}")
        return 1
    if a.write_baseline:
        os.makedirs(os.path.dirname(base_path) or ".", exist_ok=True)
        with open(base_path, "w", encoding="utf-8") as fh:
            fh.write("# factory-docs baseline: debt accepted at assessment. Shrink it, never grow it\n"
                     "# without a person's approval (factory/** is a gate path).\n")
            fh.writelines(f + "\n" for f in sorted(set(findings)))
        print(f"wrote {len(set(findings))} finding(s) to {base_path}")
        return 0
    known = set(baseline)
    new = [f for f in findings if f not in known]
    for f in findings:
        print(("known " if f in known else "NEW   ") + f)
    for f in sorted(known - set(findings)):
        print("fixed " + f + "   (remove it from the baseline)")
    for f in map_findings:
        print("NEW   " + f)
    print(f"factory-docs: {len(new) + len(map_findings)} new, {len(findings) - len(new)} baselined")
    return 1 if new or map_findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
