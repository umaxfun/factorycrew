"""Redact CI secrets from the review's outputs, and say whether any were there.

Usage: redact.py <dir> [<dir> ...]

The review agent reads files with its read tools. Every process in the job runs as the same
user, so /proc/<pid>/environ of the job's shell (which still holds every CI variable: `unset`
does not change it) is readable even when the agent's own environment is scrubbed. An injected
prompt in the diff or an issue could make the agent quote it into the transcript, the raw
stream or the usage file, which become the job log and its artifacts. This script runs after
the agent and before anything is printed or uploaded: it replaces the value of every
secret-looking variable in this environment with `[REDACTED <NAME>]` in every file under the
given directories.

Exit 0: nothing found. Exit 4: found and redacted (the names are printed, never the values);
review.sh then fails closed, because a review that read a credential is not a review.
"""

from __future__ import annotations

import os
import re
import sys

SECRET_NAME = re.compile(r"(TOKEN|PASSWORD|SECRET|PRIVATE|_KEY|KEY_|^KEY$|JWT|CREDENTIAL)", re.I)
MIN_LEN = 8  # shorter values (e.g. "true") are not secrets and would redact ordinary text


def secrets(env: dict[str, str]) -> dict[str, str]:
    """{name: value} of the variables that look like credentials."""
    return {k: v for k, v in env.items() if SECRET_NAME.search(k) and len(v) >= MIN_LEN}


def redact_dirs(dirs: list[str], env: dict[str, str]) -> set[str]:
    found: set[str] = set()
    vals = sorted(secrets(env).items(), key=lambda kv: -len(kv[1]))  # longest first
    if not vals:
        return found
    for d in dirs:
        for top, _, files in os.walk(d):
            for f in files:
                p = os.path.join(top, f)
                try:
                    with open(p, "rb") as fh:
                        data = fh.read()
                except OSError:
                    continue
                new = data
                for name, val in vals:
                    b = val.encode()
                    if b in new:
                        new = new.replace(b, f"[REDACTED {name}]".encode())
                        found.add(name)
                if new != data:
                    with open(p, "wb") as fh:
                        fh.write(new)
    return found


def main(argv: list[str]) -> int:
    found = redact_dirs([d for d in argv[1:] if os.path.isdir(d)], dict(os.environ))
    if found:
        print("redact: the review output held CI secret value(s) of " + ", ".join(sorted(found))
              + "; redacted")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
