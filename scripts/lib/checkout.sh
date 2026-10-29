# Checks on where this checkout lives (sourced by the e2e scripts).
#
# The e2e gateways run Kira's MCP server from this checkout inside Kiro Crew's macOS sandbox,
# and that sandbox denies reading another Kiro Crew instance's home: ~/.kiro/crew (the main
# gateway's, including its agents' scratch dirs) and ~/.kirocrew. A checkout there makes the
# `factorio` MCP server die on start; Kira then answers "no Factorio tools are connected" and
# the e2e fails minutes later. Seen 2026-10-29 with a worktree in ~/.kiro/crew/scratch.

checkout_readable_by_gateway() { # ROOT
  case "$1/" in
    "$HOME/.kiro/crew/"*|"$HOME/.kirocrew/"*)
      echo "$(basename "$0"): this checkout is under $1, which the e2e gateway's sandbox does not let" >&2
      echo "  Kira's MCP server read. Make the worktree elsewhere, e.g. next to the main clone:" >&2
      echo "  git -C <main clone> worktree add <main clone>.worktrees/<name> <base> (docs/worktrees.md)" >&2
      exit 2 ;;
  esac
}
