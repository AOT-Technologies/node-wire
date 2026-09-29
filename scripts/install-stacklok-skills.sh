#!/usr/bin/env bash
##
## SPDX-FileCopyrightText: 2026 AOT Technologies
## SPDX-License-Identifier: Apache-2.0
##

# install-stacklok-skills.sh — link stacklok's Phase 1 skill (ai-scoping) and the sub-agents it
# spawns (spec-analyzer, endpoint-scoper) into this repo's Claude Code or Gemini CLI config, as
# stacklok's README does. Vendored in nw-stacklok-builder/ (see UPSTREAM.md).
#
#   scripts/install-stacklok-skills.sh            # .claude/ (Claude Code)
#   scripts/install-stacklok-skills.sh gemini     # .gemini/ (Gemini CLI)
#
# Then, from the repo root: /ai-scoping <prepared spec>  (prepare it with nw gen-stacklok --path).

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-claude}" in
  claude) TARGET="$ROOT_DIR/.claude" ;;
  gemini) TARGET="$ROOT_DIR/.gemini" ;;
  *) echo "usage: $0 [claude|gemini]" >&2; exit 2 ;;
esac
SOURCE="$ROOT_DIR/nw-stacklok-builder"

mkdir -p "$TARGET/skills" "$TARGET/agents"
ln -sfn "$SOURCE/skills/ai-scoping" "$TARGET/skills/ai-scoping"
for agent in spec-analyzer endpoint-scoper; do
  ln -sfn "$SOURCE/agents/$agent.md" "$TARGET/agents/$agent.md"
done

echo "Linked into $TARGET: skills/ai-scoping, agents/spec-analyzer.md, agents/endpoint-scoper.md"
echo "Restart the AI tool, then run /ai-scoping <spec> from $ROOT_DIR"
