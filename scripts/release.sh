#!/usr/bin/env bash
# Cut a release of the Kiro Crew app (and the mod inside it). Runs in GitHub Actions only
# (.github/workflows/release.yml): Actions -> Release -> Run workflow is the one button.
#
#   scripts/release.sh bump patch|minor|major|X.Y.Z   (workflow_dispatch on main)
#   scripts/release.sh tag vX.Y.Z[-suffix]            (a pushed tag)
#
# bump: sets "version" in app/app.json, commits "Release X.Y.Z" on main, tags vX.Y.Z, builds,
#       publishes the build, pushes main and the tag.
# tag:  builds the tagged commit as that version and publishes it. A tag with a suffix
#       (v0.3.12-test.1) is a rehearsal: it goes to the branch `release-staging`, never to
#       `release`, so players do not get it. A plain vX.Y.Z must match app/app.json.
# Publish = the build committed as apps/factorycrew/ on the channel branch (`release`, a Kiro
# Crew app registry: Apps -> Discover -> Sources -> this repo, branch `release`; a newer version
# shows under Updates and the app copies the new mod into Factorio's mods folder on its next
# start), plus dist/factorycrew-<version>.zip for the GitHub release.
# FC_RELEASE_NO_PUSH=1 builds and commits locally without pushing (tests/unit/test_release.py).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
MODE="${1:-}"
ARG="${2:-}"
usage() { sed -n '2,18p' "$0"; exit 2; }
[ -n "${GITHUB_ACTIONS:-}${FC_RELEASE_NO_PUSH:-}" ] || {
  echo "releases are cut by GitHub Actions: Actions -> Release -> Run workflow (or push a tag vX.Y.Z)" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "the tree is not clean" >&2; exit 1; }

OLD="$(python3 -c "import json;print(json.load(open('app/app.json'))['version'])")"
set_version() {
  python3 - "$1" <<'EOF'
import json, sys
p = "app/app.json"
d = json.load(open(p)); d["version"] = sys.argv[1]
open(p, "w").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
EOF
}

CHANNEL=release
case "$MODE" in
  bump)
    [ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "bump releases from main" >&2; exit 1; }
    NEW="$(python3 - "$OLD" "$ARG" <<'EOF'
import re, sys
old, bump = sys.argv[1], sys.argv[2]
a, b, c = (int(x) for x in old.split("-")[0].split("."))
new = {"patch": f"{a}.{b}.{c + 1}", "minor": f"{a}.{b + 1}.0", "major": f"{a + 1}.0.0"}.get(bump, bump)
if not re.fullmatch(r"\d+\.\d+\.\d+", new):
    sys.exit(f"bad bump {bump!r}")
print(new)
EOF
)"
    if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null || git ls-remote --exit-code --tags origin "v$NEW" >/dev/null 2>&1; then
      echo "v$NEW is already tagged" >&2; exit 1
    fi
    set_version "$NEW"
    git add app/app.json
    git commit -q -m "Release $NEW"
    git tag -a "v$NEW" -m "factorycrew $NEW"
    ;;
  tag)
    [[ "$ARG" =~ ^v([0-9]+\.[0-9]+\.[0-9]+)(-[0-9A-Za-z.-]+)?$ ]] || { echo "tag must look like v1.2.3 or v1.2.3-test.1" >&2; exit 1; }
    NEW="${ARG#v}"
    if [ -n "${BASH_REMATCH[2]}" ]; then
      CHANNEL=release-staging  # a rehearsal: never reaches players
    elif [ "$NEW" != "$OLD" ]; then
      echo "tag $ARG does not match app/app.json version $OLD" >&2; exit 1
    fi
    set_version "$NEW"  # a rehearsal builds as the tag's version; for a real tag this changes nothing
    ;;
  *) usage ;;
esac
SRC="$(git rev-parse --short HEAD)"

FC_REPORT_ENV=prod ./scripts/build-app.sh >/dev/null  # players' reports go to Sentry environment `prod`
[ "$MODE" = tag ] && git checkout -q app/app.json  # the tree stays as tagged

# The channel branch holds builds only (no source history): an orphan on first use.
WT="$(mktemp -d)/channel"
git fetch -q origin "+refs/heads/$CHANNEL:refs/remotes/origin/$CHANNEL" 2>/dev/null || true
if git rev-parse -q --verify "refs/remotes/origin/$CHANNEL" >/dev/null; then
  git worktree add -q -B "$CHANNEL" "$WT" "origin/$CHANNEL"
elif git rev-parse -q --verify "refs/heads/$CHANNEL" >/dev/null; then
  git worktree add -q "$WT" "$CHANNEL"
else
  git worktree add -q --detach "$WT"
  git -C "$WT" checkout -q --orphan "$CHANNEL"
  git -C "$WT" rm -rfq --cached .
fi
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
# the branch is exactly this build: drop whatever the previous release (or main's tree) left
find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
mkdir -p "$WT/apps"
cp -R dist/factorycrew "$WT/apps/factorycrew"
cat > "$WT/README.md" <<EOF
# factorycrew $( [ "$CHANNEL" = release ] && echo "release channel" || echo "STAGING channel (rehearsals, not for players)" )

Built Kiro Crew app, version $NEW (source \`$SRC\`). Do not edit: the Release workflow
(\`.github/workflows/release.yml\`, \`scripts/release.sh\`) writes this branch.

Install: Kiro Crew 0.11 or newer, Apps -> Discover -> Sources (gear) -> add this repository with
branch \`release\`, then install **Factorio Companion**. Start Factorio with the launch option
\`--enable-lua-udp 34200\`. The app copies its mod into Factorio's mods folder; updates arrive
under Apps -> Updates, and the mod with them (restart Factorio after an update).
EOF
git -C "$WT" add -A
git -C "$WT" commit -q -m "factorycrew $NEW (from $SRC)"
echo "release $NEW: source $SRC, build on branch $CHANNEL ($(git -C "$WT" rev-parse --short HEAD)), zip dist/factorycrew-$NEW.zip"
[ -n "${GITHUB_OUTPUT:-}" ] && { echo "version=$NEW"; echo "channel=$CHANNEL"; echo "zip=dist/factorycrew-$NEW.zip"; } >> "$GITHUB_OUTPUT"

if [ -n "${FC_RELEASE_NO_PUSH:-}" ]; then
  echo "not pushed (FC_RELEASE_NO_PUSH)"
  exit 0
fi
git -C "$WT" push -q origin "HEAD:refs/heads/$CHANNEL"
if [ "$MODE" = bump ]; then
  git push -q origin HEAD:refs/heads/main "refs/tags/v$NEW"
fi
