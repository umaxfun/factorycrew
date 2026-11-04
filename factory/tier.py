#!/usr/bin/env python3
"""The tier of this merge request and the checks it needs. The CI job `factory-tier` runs it.

Usage: python3 tier.py <factory.yml from the target branch> [<factory.yml from the default branch>]

Pass the second file for an MR into a feature branch: the rules are then the default branch's,
made stricter by the feature branch's own file, exactly as the merger computes them.

Prints a dotenv on stdout (GitLab `artifacts: reports: dotenv`):
  FACTORY_IN_SCOPE=1
  FACTORY_TIER=T1
  FACTORY_CHECKS=check,e2e,ai-review-claude-opus-5-5,...   (concrete job names)
and on GitHub also appends `in_scope=`, `tier=` and `checks=,a,b,` (comma-wrapped, for
`contains()`) to $GITHUB_OUTPUT. With `scope.mode: own` (read from the default branch's file) a
merge request outside the factory's stream gets FACTORY_IN_SCOPE=0 and no tier or checks:
factory-docs and the reviews then skip themselves, and the team's merge request costs nothing.
A forge read that fails is a red job in full mode and out of scope in own mode.

Forge from the CI environment:
  GitLab: CI_API_V4_URL, CI_PROJECT_ID, CI_MERGE_REQUEST_IID, token FACTORY_READ_TOKEN
          (a read_api token: CI_JOB_TOKEN cannot read issue label events).
  GitHub: GITHUB_API_URL, GITHUB_REPOSITORY, GITHUB_EVENT_PATH (pull_request.number),
          token GITHUB_TOKEN (permissions: issues: read, pull-requests: read).

The merger computes the tier on its own and does not trust this job's output: this job only
tells the review jobs whether to run.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fcore import ConfigError, ForgeError, feature_config, in_scope, mr_tier, parse_config  # noqa: E402
from forges import GitHub, GitLab  # noqa: E402


def forge_from_env(env=os.environ, transport=None):
    if env.get("GITLAB_CI") or env.get("CI_API_V4_URL"):
        token = env.get("FACTORY_READ_TOKEN")
        if not token:
            # A fork's pipeline, or a job outside the `factory` environment, has no read token:
            # the same as an unreadable forge (out of scope in own mode, an error otherwise).
            raise ForgeError(401, "FACTORY_READ_TOKEN is not set in this job")
        forge = GitLab(env["CI_API_V4_URL"], env["CI_PROJECT_ID"], token, transport)
        return forge, int(env["CI_MERGE_REQUEST_IID"])
    if env.get("GITHUB_ACTIONS") or env.get("GITHUB_REPOSITORY"):
        api = env.get("GITHUB_API_URL") or "https://api.github.com"
        forge = GitHub(api, env["GITHUB_REPOSITORY"], env["GITHUB_TOKEN"], transport,
                       graphql=env.get("GITHUB_GRAPHQL_URL") or "")
        number = env.get("FACTORY_PR_NUMBER")
        if not number:
            with open(env["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
                number = json.load(fh)["pull_request"]["number"]
        return forge, int(number)
    raise SystemExit("tier.py: not in GitLab CI or GitHub Actions (no CI_API_V4_URL / GITHUB_REPOSITORY)")


def compute(target_text: str, default_text: str | None, forge, number: int):
    """(tier, why, jobs); tier "" and no jobs when the merge request is out of scope."""
    cfg = parse_config(target_text)
    base = parse_config(default_text) if default_text else cfg
    if default_text:
        cfg = feature_config(base, cfg)
    mr = forge.get_mr(number)
    ok, why = in_scope(forge, mr, base)
    if not ok:
        return "", why, []
    tier, why = mr_tier(forge, mr, cfg)
    jobs = [s.job for s in cfg.required(tier)]
    return tier, why, jobs


def main(argv, env=os.environ, transport=None, out=sys.stdout) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    target_text = open(argv[1], encoding="utf-8").read()
    default_text = open(argv[2], encoding="utf-8").read() if len(argv) > 2 and os.path.exists(argv[2]) else None
    try:
        forge, number = forge_from_env(env, transport)
        tier, why, jobs = compute(target_text, default_text, forge, number)
    except ConfigError as e:
        print(f"tier.py: {e}", file=sys.stderr)
        return 1
    except (ForgeError, OSError) as e:
        # Own mode: a forge read that fails (an expired read token, or DNS, a refused connection
        # or a timeout, which urllib raises as OSError) must not put a red job on
        # every team merge request. Out of scope here skips the reviews; the merger reads the
        # forge on its own and merges nothing whose required reviews did not pass.
        if parse_config(default_text or target_text).scope_mode != "own":
            raise
        tier, why, jobs = "", f"the forge could not be read ({e}): check FACTORY_READ_TOKEN", []
    scope = "1" if tier else "0"
    if tier:
        print(f"{tier}: {why}; required checks: {', '.join(jobs)}", file=sys.stderr)
    else:
        print(f"out of the factory's scope ({why}): factory-docs and the AI reviews skip", file=sys.stderr)
    print(f"FACTORY_IN_SCOPE={scope}", file=out)
    print(f"FACTORY_TIER={tier}", file=out)
    print("FACTORY_CHECKS=" + ",".join(jobs), file=out)
    gh_out = env.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"in_scope={scope}\ntier={tier}\nchecks=,{','.join(jobs)},\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
