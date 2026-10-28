#!/usr/bin/env bash
# Build the redistributable Kiro Crew app: dist/factorycrew/ (and dist/factorycrew-<version>.zip).
#
# Install it into a gateway with `kirocrew app install dist/factorycrew`, then
# `kirocrew app enable factorycrew`. The folder is self-contained: mod, bridge (run by the
# app backend), MCP server, dispatcher cron, the Kira agent and its skills.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/dist/factorycrew"
VERSION="$(python3 -c "import json;print(json.load(open('$ROOT/app/app.json'))['version'])")"

rm -rf "$OUT"
mkdir -p "$OUT"/{agents,assets,backend,bridge,mcp,dispatcher,mod}
cp "$ROOT/app/app.json" "$OUT/"
cp "$ROOT/app/backend/server.py" "$OUT/backend/"
cp "$ROOT/bridge/factorio_bridge.py" "$OUT/bridge/"
cp "$ROOT/mcp/factorio_mcp.py" "$ROOT/mcp/kc_compute.py" "$OUT/mcp/"
cp "$ROOT/dispatcher/factorio_dispatch.py" "$OUT/dispatcher/"
cp "$ROOT/tools/kira-256.png" "$OUT/assets/icon.png"
rsync -a --exclude tests --exclude '.*' "$ROOT/mod/kc-companion/" "$OUT/mod/kc-companion/"
[ -d "$ROOT/app/skills" ] && cp -R "$ROOT/app/skills" "$OUT/skills"

# The agent: an app agent cannot point a file:// resource into its own install dir, so the
# rules are folded into the prompt. Its MCP server is the app's own, `@<app>:<server>`.
python3 - "$ROOT" "$OUT" <<'EOF'
import json, sys
from pathlib import Path
root, out = Path(sys.argv[1]), Path(sys.argv[2])
spec = json.loads((root / "agent" / "factorio-companion.json").read_text())
rules = (root / "agent" / "factorio-companion.md").read_text()
agent = {
    "name": "factorio-companion",
    "description": spec["description"],
    "prompt": spec["prompt"].replace("Follow the rules in factorio-companion.md. ", "") + "\n\n" + rules,
    "tools": ["@factorycrew:factorio"],
    "allowedTools": ["@factorycrew:factorio"],
    "includeMcpJson": False,
}
(out / "agents" / "factorio-companion.json").write_text(json.dumps(agent, ensure_ascii=False, indent=2) + "\n")
EOF

# Mod version must match the app's so the backend's "already installed" check sees updates.
python3 - "$OUT" "$VERSION" <<'EOF'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / "mod" / "kc-companion" / "info.json"
info = json.loads(p.read_text()); info["version"] = sys.argv[2]
p.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n")
EOF

(cd "$ROOT/dist" && rm -f "factorycrew-$VERSION.zip" && zip -qr "factorycrew-$VERSION.zip" factorycrew)
echo "built $OUT ($VERSION) and dist/factorycrew-$VERSION.zip"
