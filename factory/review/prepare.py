#!/usr/bin/env python3
"""Build the review prompt and its input files. review.sh runs it; it reads only git and files.

Usage: prepare.py --repo <checkout> --ref <target ref, e.g. origin/main> --target <branch>
                  --head <sha> --base <sha> --out <dir>
                  [--source <branch> --source-ref <ref, e.g. origin/feature/x>]
                  [--agent-repo <dir> --agent-out <dir>]   (the paths the prompt names)

Reads from the TARGET ref, never from the merge request: factory.yml, the calibration file,
the architecture overview, the contract docs of the areas the diff touches (factory.yml
`areas`), and for a feature branch its designs folder. A feature branch going into the default
branch is judged with the designs folder of the SOURCE branch as the forge holds it
(--source-ref), because the design was approved there and reaches the default branch only with
this merge request; the head commit's copy is never used. When the target has no factory.yml yet
(the merge request that adds the factory), the checkout's copy is used and the run says so;
that merge request touches gate paths, so a person approves it.

From the forge (the CI environment and token tier.py uses: FACTORY_READ_TOKEN on GitLab,
GITHUB_TOKEN on GitHub) it reads the issues the merge request closes, by the rule tier.py
uses, with each issue's author and effective tier; `humans` and the tier rules come from the
default branch's factory.yml. When the forge cannot be read the review runs without them and
says so.

Writes into --out: shots/shot-NN.<ext> (for the ux lens: the screenshots the forge hosts, see
shots.py), prompt.md, repo-context.md, calibration.md, design.md, issues.md (the
closed issues' text, UNTRUSTED), ledger.md (prior rulings on this merge request, fenced,
UNTRUSTED), inputs.txt (what was read from where). The prompt modules are this script's own `prompt/` folder, which
review.sh took from the target branch together with this script.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.join(HERE, ".."), os.path.join(HERE, "..", "factory")):  # vendored, package
    if os.path.isfile(os.path.join(p, "fcore.py")):
        sys.path.insert(0, os.path.abspath(p))
        break

from fcore import (Config, ConfigError, ForgeError, feature_config, is_crew_author, issue_tier, load_yaml, match_any,  # noqa: E402
                   parse_config, review_job, triage_citation)

MODULES = ["00-rules.md", "10-repo-context.md", "20-fan-out.md", "25-convergence.md", "30-falsify.md",
           "35-adjudicate.md", "40-output.md"]


def git(repo: str, *args: str) -> str | None:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def show(repo: str, ref: str, path: str) -> str | None:
    return git(repo, "show", f"{ref}:{path}")


def touched_areas(cfg: Config, paths: list[str]) -> list[dict]:
    return [a for a in cfg.areas if any(match_any(p, a["paths"]) for p in paths)]


def lens_text(lenses: list[str]) -> str:
    parts = []
    for i, name in enumerate(lenses, 1):
        path = os.path.join(HERE, "prompt", "lenses", f"{name}.md")
        if not os.path.isfile(path):
            raise SystemExit(f"prepare.py: unknown review lens {name!r} (no prompt/lenses/{name}.md)")
        body = open(path, encoding="utf-8").read().strip()
        parts.append(f"{i}. " + body.replace("\n", "\n   "))
    return "\n\n".join(parts)


ISSUE_BODY_MAX = 20000


def forge_context(forge, number: int, target_cfg: Config, log: list):
    """(merge request, rules config) from the forge, or (None, None) when it cannot be read.

    `humans` and the tier rules come from the default branch's factory.yml (made stricter by the
    target's, as the merger does), never from a feature branch alone: a crew-pushed branch must
    not add itself to `humans`.
    """
    if forge is None:
        return None, None
    try:
        default_text = forge.read_file("factory.yml", forge.default_branch())
        cfg = target_cfg
        if default_text is not None:
            cfg = feature_config(parse_config(default_text), target_cfg)
            log.append("forge: humans and tiers from the default branch's factory.yml")
        else:
            log.append("forge: no factory.yml on the default branch, humans and tiers from the target's")
        return forge.get_mr(number), cfg
    except (ForgeError, ConfigError, KeyError, ValueError, OSError) as e:
        log.append(f"forge: merge request NOT read ({e})")
        return None, None


EFFECT_RULE = {
    "fix": "the change may only bring the code back to the cited spec line",
    "addition": "the change may add, never change existing behaviour",
    "change": "the change must be covered by the approved design",
    "concept": "the change must be covered by the approved full design",
}


def _norm(text: str) -> str:
    return " ".join(text.split()).strip("`*_ ").lower()


def citation_fact(forge, n: int, cfg: Config, repo: str | None, ref: str | None) -> tuple[str, str]:
    """(prompt fact, issues.md line) for the spec line the triage comment of issue #n cites.

    The path and line number go into the prompt (they are checked to be a repository path and a
    number); the quote is the forge's text and goes only into issues.md. Whether the quote is
    on that line of the target branch is computed here, so the review does not take the
    triager's word for it.
    """
    try:
        cite = triage_citation(forge.issue_comments(n), cfg)
    except (ForgeError, NotImplementedError, KeyError, TypeError, ValueError, OSError):
        return "triage citation: NOT read", ""
    if cite is None:
        return "triage citation: none", ""
    where = f"`{cite.path}:{cite.line}`"
    found = "not checked"
    if repo and ref:
        text = show(repo, ref, cite.path)
        if text is None:
            found = f"no such file on {ref}"
        else:
            lines = text.splitlines()
            near = " ".join(lines[max(cite.line - 1, 0):cite.line + 2]) if cite.line >= 1 else ""
            found = "yes" if cite.line <= len(lines) and _norm(cite.quote) in _norm(near) else "no"
    return (f"triage citation: {where} (from `{cite.author}`; the quote is on that line of the target: {found})",
            f"Triage citation (from `{cite.author}`): {cite.path}:{cite.line} \"{cite.quote}\"")


def closed_issues(forge, mr, cfg: Config, log: list, repo: str | None = None,
                  ref: str | None = None) -> tuple[str, str]:
    """(prompt facts, issues.md text) for the issues the merge request closes.

    The facts (number, author, whether the author is in `humans`, tier, what the tier lets the
    change do, the triager's spec citation) are computed here and go into the prompt; the
    title, body and quoted spec line are the forge's text and go only into issues.md.
    """
    if mr is None:
        log.append("closed issues: NOT read (see forge above)")
        return "- The issues this merge request closes could not be read (see inputs.txt).", ""
    try:
        # Only open issues are decisions in force, as in fcore.mr_tier.
        numbers = [n for n in forge.closing_issues(mr) if forge.issue_is_open(n)]
        facts, texts = [], []
        for n in numbers:
            issue = forge.issue(n)
            tier, why = issue_tier(forge.label_events(n), cfg)
            person = issue.author in cfg.humans
            design = cfg.design_tier
            below = design is not None and cfg.tiers.index(tier) < cfg.tiers.index(design)
            effect = cfg.tier[tier]["effect"]
            cited, cited_text = citation_fact(forge, n, cfg, repo, ref)
            line = (f"#{n}: author `{issue.author}` (a person in `humans`: {'yes' if person else 'no'}); "
                    f"tier {tier} ({why}); first tier that needs a design: {design or 'none'}; "
                    f"below it: {'yes' if below else 'no'}; effect {effect}: {EFFECT_RULE[effect]}; "
                    f"{cited}")
            facts.append("- " + line)
            body = issue.body if len(issue.body) <= ISSUE_BODY_MAX else issue.body[:ISSUE_BODY_MAX] + "\n[truncated]"
            extra = f"\n\n{cited_text}" if cited_text else ""
            texts.append(f"===== issue {line} =====\nTitle: {issue.title}\n\n{body}{extra}")
        log.append(f"closed issues: {', '.join(f'#{n}' for n in numbers) or 'none'}")
    except (ForgeError, ConfigError, KeyError, ValueError, OSError) as e:
        log.append(f"closed issues: NOT read ({e})")
        return "- The issues this merge request closes could not be read (see inputs.txt).", ""
    if not facts:
        return "- This merge request closes no issue.", ""
    return "\n".join(facts), "\n\n".join(texts) + "\n"


def borderline(forge, cfg: Config | None, log: list) -> str:
    """The `triage.borderline` cases of the default branch's factory.yml, as prompt facts.

    Only the default branch's copy counts, as for `humans`: a merge request or a crew-pushed
    feature branch must not record a decision that excuses its own change.
    """
    if forge is None or cfg is None:
        log.append("borderline decisions: NOT read (no forge)")
        return "- None could be read."
    try:
        text = forge.read_file("factory.yml", forge.default_branch())
        cases = ((load_yaml(text) or {}).get("triage") or {}).get("borderline") or [] if text else []
    except (ForgeError, ConfigError, KeyError, ValueError, OSError, AttributeError) as e:
        log.append(f"borderline decisions: NOT read ({e})")
        return "- None could be read."
    design = cfg.design_tier
    lines = []
    for c in cases if isinstance(cases, list) else []:
        if not isinstance(c, dict) or not c.get("case") or c.get("tier") not in cfg.tiers:
            continue
        below = design is not None and cfg.tiers.index(c["tier"]) < cfg.tiers.index(design)
        case = " ".join(str(c["case"]).split())[:200]
        lines.append(f"- \"{case}\": tier {c['tier']}; below the first tier that needs a design: "
                     f"{'yes' if below else 'no'}")
    log.append(f"borderline decisions: {len(lines)}")
    return "\n".join(lines) or "- None recorded."


# ---------------------------------------------------------------- round convergence

LEDGER_MAX = 6000
LEDGER_LINES = 12
DISPOSITION = "<!-- factory-disposition"
OVERRIDE_LINE = re.compile(r"^\s*/factory\s+override\s+ai-review\b", re.I)
RULING_LINE = re.compile(r"^(?:<!--|> |\s*[-*]\s*\*\*)")


def read_comments(forge, mr, log: list):
    """The merge request's comments, or None when they cannot be read (said in inputs.txt)."""
    if mr is None:
        return None
    try:
        return forge.comments(mr)
    except (ForgeError, KeyError, ValueError, OSError) as e:
        log.append(f"comments: NOT read ({e})")
        return None


def ledger(mr, comments, cfg: Config, log: list, crew_author: bool = True) -> tuple[str, str]:
    """(the prompt's sentence about it, ledger.md text): prior rulings on THIS merge request.

    Two kinds of record, newest first, each capped at LEDGER_LINES lines and the whole at
    LEDGER_MAX bytes: `/factory override ai-review ...` lines, and disposition comments (a
    comment that starts with `<!-- factory-disposition`) of which only the marker, the verdict
    bullets (`- **fixed** ...`, `- **rebutted** ...`) and quoted rationale lines (`> ...`) are
    kept. Only from a person in `humans` or the merge request's author (the crew): anyone can
    comment, so the author is what carries the authority, and the prompt tells the two apart.
    A ledger that cannot be read is said; the review then runs without it (it re-raises more,
    never less).
    """
    if comments is None:
        log.append("ledger: NOT read (no comments, see above)")
        return ("Empty: the comments of this merge request could not be read (see inputs.txt), so "
                "there are no prior rulings to apply."), ""
    records = []
    for c in reversed(comments):
        person = c.author in cfg.humans
        crew = crew_author and bool(c.author) and c.author == mr.author
        if not (person or crew):
            continue
        lines = (c.body or "").splitlines()
        keep = [ln.strip() for ln in lines if OVERRIDE_LINE.match(ln)]
        if lines and lines[0].lstrip().startswith(DISPOSITION):
            keep += [ln.rstrip() for ln in lines if RULING_LINE.match(ln)]
        if keep:
            who = "a person in `humans`" if person else "the merge request's author (the crew)"
            records.append(f"@ by `{c.author}`, {who}\n" + "\n".join(keep[:LEDGER_LINES]))
    text = "\n\n".join(records)
    if len(text.encode()) > LEDGER_MAX:
        text = text.encode()[:LEDGER_MAX].decode("utf-8", "ignore")
        text += "\n[ledger truncated: records are newest first, so only OLDER records are cut]"
    log.append(f"ledger: {len(records)} record(s) of prior rulings")
    if not records:
        return "Empty: nobody has ruled on a finding of this merge request yet.", ""
    nonce = os.urandom(6).hex()
    return (f"{len(records)} record(s) of prior rulings on THIS merge request, newest first, "
            f"between `LEDGER_BEGIN::{nonce}` and `LEDGER_END::{nonce}`."), (
        f"LEDGER_BEGIN::{nonce}\n{text}\nLEDGER_END::{nonce}\n")


def forge_from_env(env=os.environ, transport=None):
    """(forge, merge request number) from the CI environment, as tier.py builds them; (None, why) outside CI."""
    import tier  # noqa: PLC0415 - next to fcore.py, vendored or in the package

    try:
        return tier.forge_from_env(env, transport)
    except (SystemExit, KeyError, ValueError, OSError, tier.ForgeError) as e:
        return None, f"{type(e).__name__}: {e}"


def _design_files(repo: str, cfg: Config, ref: str, label: str) -> list[str]:
    listing = git(repo, "ls-tree", "-r", "--name-only", ref, "--", cfg.designs_dir + "/") or ""
    return [f"===== {f} ({label}) =====\n{show(repo, ref, f) or ''}"
            for f in listing.splitlines() if f.endswith(".md")]


def _commit(repo: str, ref: str) -> str | None:
    out = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return out.strip() if out else None


DESIGN_NONE = ("Empty: this merge request neither targets a feature branch nor ships one into the "
               "default branch.")


def feature_design(repo: str, cfg: Config, ref: str, target: str, head: str, source: str,
                   source_ref: str, log: list) -> tuple[list[str], str]:
    """(design.md parts, the prompt's sentence about them).

    Into a feature branch: the designs folder of the target. A feature branch into the default
    branch: the designs folder of the SOURCE branch as the forge holds it (`source_ref`, e.g.
    origin/feature/x), never the merge request's head: the design got there through a merge
    request a person approved, and arrives on the default branch only with this one.
    """
    if cfg.is_feature(target):
        design = _design_files(repo, cfg, ref, f"from {target}")
        log.append(f"feature design: {len(design)} file(s) from {ref}:{cfg.designs_dir}/")
        return design, (
            f"The designs folder of the feature branch `{target}` this merge request targets. When "
            "it is not empty, this merge request is ONE STEP of a larger feature whose design a "
            "person approved. Judge the step against that design: a gap the design assigns to a "
            "LATER step is not a finding, and the merge request doing exactly what its step asks "
            "is correct. A step that contradicts the design, breaks something that works today, "
            "or ships a user-visible promise the design does not make IS a finding.")
    if not (source and target == cfg.default_branch and cfg.is_feature(source)):
        return [], DESIGN_NONE
    tip = _commit(repo, source_ref) if source_ref else None
    if tip is None:
        log.append(f"feature design: NOT read (source branch {source} has no readable ref "
                   f"{source_ref or '(none given)'})")
        return [], (f"Empty: this merge request ships the feature branch `{source}`, but its "
                    "approved design could not be read (see inputs.txt). Judge the product shape "
                    "without it.")
    design = _design_files(repo, cfg, source_ref, f"the feature design, approved into {source}")
    log.append(f"feature design: {len(design)} file(s) from {source_ref}:{cfg.designs_dir}/ "
               f"(source branch {source}, approved there)")
    note = ""
    head_commit = _commit(repo, head)
    if head_commit != tip:
        log.append(f"feature design: the merge request head {head} is not the tip of {source_ref} "
                   f"({tip}); the design is read from {source_ref}")
        note = (f" The merge request's head `{head}` is not the tip of `{source_ref}` (`{tip}`): "
                f"the design is read from `{source_ref}`, not from the head.")
    return design, (
        f"This merge request ships the whole feature branch `{source}` into `{target}`. "
        f"`design.md` holds the designs folder as `{source_ref}` has it: the feature design, "
        f"approved into `{source}` through a merge request a person approved there (the designs "
        f"folder is a gate path). It reaches `{target}` with this very merge request, so it is not "
        "on the target yet: count it as the recorded decision for what it covers. Anything the "
        "diff ships beyond what the design covers is still a finding." + note)


BLIND_READ = """
## Step 0: blind read of the screenshots (before anything else)

The review job saved __N__ screenshot(s) attached to the merge request: __FILES__. Before you
open the diff, the intent or any other input, spawn ONE subagent and give it ONLY these image
paths and this task, nothing else (no diff, no intent, no file or branch names, no lens
charter): "You see the screens of an app for the first time. For each image: what the screen is
for; every control you can see, with its label exactly as written and what you expect it to do
when used; every label, icon or state whose meaning you cannot tell. Do not guess from file
names. One plain block per image." Keep its report verbatim as the BLIND READ: give it to the
`ux` lens below with its charter. Do not look at the images yourself before the report is back.
"""


PUSH_BUDGET_MIN = 1  # earlier blocked heads: from the second blocked round on


def push_budget(forge, mr, cfg, log) -> int | None:
    """How many earlier heads of this merge request a review job blocked; None when unreadable."""
    if mr is None:
        log.append("push budget: NOT counted (see forge above)")
        return None
    try:
        n = len(forge.blocked_review_heads(mr, {review_job(m) for m in cfg.review_models}))
    except (ForgeError, NotImplementedError, KeyError, ValueError, OSError) as e:
        log.append(f"push budget: NOT counted ({e}); the section is left out")
        return None
    log.append(f"push budget: {n} earlier blocked head(s)"
               + ("; the section is in the prompt" if n >= PUSH_BUDGET_MIN else ""))
    return n


def screenshots(forge, mr, comments, cfg, lenses, ui, shots_dir, agent_out, log, fetch=None):
    """(the blind read step for 20-fan-out, the evidence sentence for the ux lens).

    Images are read only for the `ux` lens, only from the description and from comments by a
    person in `humans` or the merge request's author: a stranger's image must not decide what
    the reviewer believes the user sees.
    """
    if "ux" not in lenses:
        return "", ""
    saved, failed = [], 0
    if mr is not None and comments is not None:
        import shots  # noqa: PLC0415 - next to this script

        texts = [mr.body] + [c.body for c in comments if c.author in cfg.humans or c.author == mr.author]
        kw = {"fetch": fetch} if fetch else {}
        saved, failed = shots.download(forge, texts, shots_dir, log, **kw)
    else:
        log.append("screenshots: NOT read (no merge request or comments, see above)")
    blind = ""
    if saved:
        files = ", ".join(f"`{agent_out}/shots/{n}`" for n in saved)
        blind = BLIND_READ.replace("__N__", str(len(saved))).replace("__FILES__", files)
    if not ui:
        return blind, ("This diff changes no file under `evidence.screenshot_when`: do not ask for "
                       "screenshots and raise no evidence gap.")
    listed = ", ".join(f"`{p}`" for p in ui[:6]) + (" ..." if len(ui) > 6 else "")
    if failed or mr is None or comments is None:
        return blind, (f"This diff changes what users see ({listed}), but not every screenshot "
                       "attached to the merge request could be read (see inputs.txt): a missing "
                       "screenshot is at most one FINDING (evidence gap), never blocking.")
    return blind, (f"This diff changes what users see ({listed}). A NEW user-visible control this "
                   "diff adds (a button, link, input, menu entry, toggle, tab or label) that appears "
                   "in NO screenshot of the BLIND READ, or that has no screenshot at all, is a "
                   "candidate with anchor `evidence-gap`: name the control and the file:line that "
                   "renders it; `Clears when:` a screenshot showing it is attached to the merge "
                   "request.")


def build(repo: str, ref: str, target: str, head: str, base: str, out: str,
          forge=None, number: int | None = None, forge_note: str = "not in CI",
          source: str = "", source_ref: str = "", agent_repo: str = "", agent_out: str = "",
          fetch=None) -> dict:
    os.makedirs(out, exist_ok=True)
    log = []
    text = show(repo, ref, "factory.yml")
    bootstrap = text is None
    if text is None:
        text = open(os.path.join(repo, "factory.yml"), encoding="utf-8").read()
        log.append(f"factory.yml: not on {ref} yet, using the merge request's copy (bootstrap)")
    else:
        log.append(f"factory.yml: {ref}")
    cfg = parse_config(text)
    paths = [p for p in (git(repo, "diff", "--name-only", f"{base}...{head}") or "").splitlines() if p]

    ctx = []
    if cfg.overview:
        doc = show(repo, ref, cfg.overview)
        log.append(f"overview {cfg.overview}: {'read' if doc is not None else 'MISSING on ' + ref}")
        if doc is not None:
            ctx.append(f"===== {cfg.overview} (overview, from {ref}) =====\n{doc}")
    areas = touched_areas(cfg, paths)
    for a in areas:
        doc = show(repo, ref, a["doc"])
        log.append(f"area {a['name']} -> {a['doc']}: {'read' if doc is not None else 'MISSING on ' + ref}")
        if doc is not None:
            ctx.append(f"===== {a['doc']} (area: {a['name']}, from {ref}) =====\n{doc}")
    if not areas:
        log.append("areas: the diff touches no area in factory.yml")
    with open(os.path.join(out, "repo-context.md"), "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(ctx) + "\n")

    cal = show(repo, ref, cfg.calibration)
    if cal is None and bootstrap and os.path.isfile(os.path.join(repo, cfg.calibration)):
        # The merge request that adds the factory also adds its calibration: use it, like
        # factory.yml above. That merge request touches gate paths, so a person approves it.
        cal = open(os.path.join(repo, cfg.calibration), encoding="utf-8").read()
        log.append(f"calibration {cfg.calibration}: not on {ref} yet, using the merge request's copy (bootstrap)")
    else:
        log.append(f"calibration {cfg.calibration}: {'read' if cal is not None else 'none on ' + ref}")
    with open(os.path.join(out, "calibration.md"), "w", encoding="utf-8") as fh:
        fh.write(cal or "")

    design, design_note = feature_design(repo, cfg, ref, target, head, source, source_ref, log)
    with open(os.path.join(out, "design.md"), "w", encoding="utf-8") as fh:
        fh.write("\n\n".join(design))

    if forge is None:
        log.append(f"forge: NOT read (no forge: {forge_note})")
    mr, rules = forge_context(forge, number, cfg, log)
    issue_facts, issue_text = closed_issues(forge, mr, rules, log, repo, ref)
    decided = borderline(forge if mr is not None else None, rules, log)
    with open(os.path.join(out, "issues.md"), "w", encoding="utf-8") as fh:
        fh.write(issue_text)
    comments = read_comments(forge, mr, log)
    # The author's dispositions are the crew's only when the merger would also take the author as
    # the crew (fcore.is_crew_author). Unreadable: not the crew; the review then re-raises more.
    try:
        crew_author = mr is not None and forge is not None and is_crew_author(forge, mr, rules)
    except ForgeError as e:
        log.append(f"ledger: author not taken as the crew ({e})")
        crew_author = False
    ledger_note, ledger_text = ledger(mr, comments, rules, log, crew_author)
    with open(os.path.join(out, "ledger.md"), "w", encoding="utf-8") as fh:
        fh.write(ledger_text)
    # The effective rules (the default branch's, made stricter by a feature branch) decide the
    # lenses and the screenshot rule; the target's own config may only add to them.
    eff = rules or cfg
    lenses = list(eff.review_lenses) + [x for x in cfg.review_lenses if x not in eff.review_lenses]
    shot_when = list(eff.screenshot_when) + [g for g in cfg.screenshot_when if g not in eff.screenshot_when]
    shot_except = list(eff.screenshot_except)
    ui = sorted({p for p in paths if match_any(p, shot_when)
                 and not match_any(p, shot_except)}) if shot_when else []
    blind, ux_evidence = screenshots(forge, mr, comments, rules, lenses, ui,
                                     os.path.join(out, "shots"), agent_out or out, log, fetch)

    blocked = push_budget(forge, mr, rules, log)
    modules = list(MODULES)
    if blocked is not None and blocked >= PUSH_BUDGET_MIN:
        modules.insert(modules.index("40-output.md"), "38-push-budget.md")
    prompt = "".join(open(os.path.join(HERE, "prompt", m), encoding="utf-8").read() for m in modules)
    fill = {"__LENSES__": lens_text(lenses), "__LENS_COUNT__": str(len(lenses)),
            "__LENS_NAMES__": ",".join(f'"{x}"' for x in lenses),
            "__AREAS__": ", ".join(a["name"] for a in areas) or "none", "__ISSUES__": issue_facts,
            "__BORDERLINE__": decided,
            "__DESIGN__": design_note, "__LEDGER__": ledger_note,
            "__BLIND_READ__": blind, "__UX_EVIDENCE__": ux_evidence, "__BLOCKED__": str(blocked or 0),
            "__HEAD_SHA__": head, "__BASE_SHA__": base, "__TARGET__": target,
            # The paths the agent reads them at: review.sh hands it copies in a workspace of
            # its own (confine.py), so these differ from --repo/--out in CI.
            "__OUT__": agent_out or out, "__REPO__": agent_repo or os.path.abspath(repo)}
    for k, v in fill.items():
        prompt = prompt.replace(k, v)
    with open(os.path.join(out, "prompt.md"), "w", encoding="utf-8") as fh:
        fh.write(prompt)
    log.append(f"lenses: {', '.join(lenses)}")
    with open(os.path.join(out, "inputs.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log) + "\n")
    return {"lenses": lenses, "areas": [a["name"] for a in areas], "log": log}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    for k in ("repo", "ref", "target", "head", "base", "out"):
        ap.add_argument(f"--{k}", required=True)
    ap.add_argument("--source", default="")
    ap.add_argument("--source-ref", default="")
    ap.add_argument("--agent-repo", default="", help="where the agent reads the checkout copy")
    ap.add_argument("--agent-out", default="", help="where the agent reads the inputs")
    a = ap.parse_args(argv[1:])
    forge, number = forge_from_env()
    note = "" if forge is not None else number
    if forge is None:
        number = None
    res = build(a.repo, a.ref, a.target, a.head, a.base, a.out, forge, number, note,
                a.source, a.source_ref, a.agent_repo, a.agent_out)
    print("\n".join(res["log"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
