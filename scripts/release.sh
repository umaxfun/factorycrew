#!/usr/bin/env bash
# Cut a release of the Kiro Crew app (and the mod inside it).
#
#   scripts/release.sh patch|minor|major|X.Y.Z|current [--push]
#
# 1. bumps "version" in app/app.json (the mod's info.json gets the same version at build time),
#    commits "Release X.Y.Z" on main and tags vX.Y.Z (`current` skips the bump: first release);
# 2. builds dist/factorycrew with scripts/build-app.sh;
# 3. commits the build to the `release` branch as apps/factorycrew/. That branch is a Kiro Crew
#    app registry: Apps -> Discover -> Sources -> add the repo URL with branch `release`. A newer
#    version there shows up under Updates, and the app's backend copies the new mod into the
#    player's Factorio mods folder on its next start (the mod never comes from the mod portal);
# 4. with --push, pushes main, release and the tag to `origin`. Without it nothing leaves the machine.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
BUMP="${1:-}"
PUSH=0
[ "${2:-}" = "--push" ] && PUSH=1
case "$BUMP" in patch|minor|major|current) ;; [0-9]*.[0-9]*.[0-9]*) ;; *)
  sed -n '2,14p' "$0"; exit 2 ;;
esac

[ "$(git rev-parse --abbrev-ref HEAD)" = main ] || { echo "release from main" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first" >&2; exit 1; }

OLD="$(python3 -c "import json;print(json.load(open('app/app.json'))['version'])")"
NEW="$(python3 - "$OLD" "$BUMP" <<'EOF'
import sys
old, bump = sys.argv[1], sys.argv[2]
a, b, c = (int(x) for x in old.split("-")[0].split("."))
print({"patch": f"{a}.{b}.{c + 1}", "minor": f"{a}.{b + 1}.0", "major": f"{a + 1}.0.0",
       "current": old}.get(bump, bump))
EOF
)"
if git rev-parse -q --verify "refs/tags/v$NEW" >/dev/null; then echo "v$NEW is already tagged" >&2; exit 1; fi

if [ "$NEW" != "$OLD" ]; then
  python3 - "$NEW" <<'EOF'
import json, sys
p = "app/app.json"
d = json.load(open(p)); d["version"] = sys.argv[1]
open(p, "w").write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
EOF
  git add app/app.json
  git commit -q -m "Release $NEW"
fi
git tag -a "v$NEW" -m "factorycrew $NEW"
SRC="$(git rev-parse --short HEAD)"

./scripts/build-app.sh >/dev/null

# The release branch holds builds only (no source history): an orphan on first use.
WT="$(mktemp -d)/release"
if git rev-parse -q --verify refs/heads/release >/dev/null; then
  git worktree add -q "$WT" release
else
  git worktree add -q --detach "$WT"
  git -C "$WT" checkout -q --orphan release
  git -C "$WT" rm -rfq --cached .
fi
trap 'git worktree remove --force "$WT" >/dev/null 2>&1 || true' EXIT
# the branch is exactly this build: drop whatever the previous release (or main's tree) left
find "$WT" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
mkdir -p "$WT/apps"
cp -R dist/factorycrew "$WT/apps/factorycrew"
cat > "$WT/README.md" <<EOF
# factorycrew release channel

Built Kiro Crew app, version $NEW (source \`$SRC\` on \`main\`). Do not edit: \`scripts/release.sh\` on
\`main\` writes this branch.

Install: Kiro Crew 0.11 or newer, Apps -> Discover -> Sources (gear) -> add this repository with
branch \`release\`, then install **Factorio Companion**. Start Factorio with the launch option
\`--enable-lua-udp 34200\`. The app copies its mod into Factorio's mods folder; updates arrive
under Apps -> Updates, and the mod with them (restart Factorio after an update).
EOF
git -C "$WT" add -A
git -C "$WT" commit -q -m "factorycrew $NEW (from $SRC)"
echo "release $NEW: tag v$NEW on main ($SRC), build on branch release ($(git rev-parse --short release))"

if [ "$PUSH" = 1 ]; then
  git remote get-url origin >/dev/null 2>&1 || { echo "no origin remote; add it, then push main release v$NEW" >&2; exit 1; }
  git push origin main release "v$NEW"
else
  echo "not pushed; later: git push origin main release v$NEW"
fi
