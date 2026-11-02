"""scripts/release.sh as the Release workflow runs it (.github/workflows/release.yml), against a
copy of this checkout with a bare repository as `origin`. ~10 s."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def git(cwd, *args, check=True):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A one-commit copy of this checkout on `main`, pushed to a bare `origin`."""
    work, origin = tmp_path / "work", tmp_path / "origin.git"
    files = git(ROOT, "ls-files", "-co", "--exclude-standard").splitlines()
    for f in files:
        src = ROOT / f
        if src.is_file() and ".test-data" not in f and not f.startswith("dist/"):
            (work / f).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, work / f)
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(work, "init", "-q", "-b", "main")
    git(work, "config", "user.email", "t@t")
    git(work, "config", "user.name", "t")
    git(work, "add", "-A")
    git(work, "commit", "-qm", "base")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "main")
    return work, origin


def release(work, *args, env=None):
    e = {k: v for k, v in os.environ.items() if k not in ("GITHUB_ACTIONS", "FC_RELEASE_NO_PUSH", "GITHUB_OUTPUT")}
    e.update({"GITHUB_ACTIONS": "true"} if env is None else env)
    return subprocess.run([str(work / "scripts" / "release.sh"), *args], cwd=work, capture_output=True, text=True,
                          env=e, timeout=120)


def version(work, ref):
    return json.loads(git(work, "show", f"{ref}:apps/factorycrew/app.json"))["version"]


def test_releases_are_refused_outside_ci(repo):
    work, _ = repo
    p = release(work, "bump", "patch", env={})
    assert p.returncode == 1 and "Actions -> Release" in p.stderr


def test_the_button_bumps_main_tags_and_publishes_the_release_channel(repo):
    work, origin = repo
    old = json.loads((work / "app" / "app.json").read_text())["version"]
    a, b, c = (int(x) for x in old.split("."))
    new = f"{a}.{b}.{c + 1}"
    p = release(work, "bump", "patch")
    assert p.returncode == 0, p.stderr
    assert json.loads(git(origin, "show", "main:app/app.json"))["version"] == new
    assert git(origin, "log", "-1", "--format=%s", "main") == f"Release {new}"
    assert git(origin, "rev-parse", f"v{new}^{{commit}}") == git(origin, "rev-parse", "main")
    assert version(origin, "release") == new
    assert git(origin, "show", "release:apps/factorycrew/report/environment") == "prod"
    assert (work / "dist" / f"factorycrew-{new}.zip").exists()
    assert release(work, "bump", new).returncode == 1  # a version already tagged


def test_a_suffixed_tag_is_a_rehearsal_on_staging_only(repo):
    work, origin = repo
    old = json.loads((work / "app" / "app.json").read_text())["version"]
    git(work, "tag", "v9.9.9-test.1")
    git(work, "checkout", "-q", "v9.9.9-test.1")  # as actions/checkout leaves a tag build
    p = release(work, "tag", "v9.9.9-test.1")
    assert p.returncode == 0, p.stderr
    assert version(origin, "release-staging") == "9.9.9-test.1"
    assert git(origin, "branch", "--list", "release") == ""  # players' channel untouched
    assert json.loads((work / "app" / "app.json").read_text())["version"] == old and git(work, "status", "--porcelain") == ""


def test_a_plain_tag_must_match_the_app_version(repo):
    work, origin = repo
    old = json.loads((work / "app" / "app.json").read_text())["version"]
    assert release(work, "tag", "v99.0.0").returncode == 1
    p = release(work, "tag", f"v{old}")
    assert p.returncode == 0, p.stderr
    assert version(origin, "release") == old and git(origin, "branch", "--list", "release-staging") == ""
