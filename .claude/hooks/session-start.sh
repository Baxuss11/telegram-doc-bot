#!/usr/bin/env bash
# Ставит инструменты автомонтажа в облачной сессии Claude Code (claude.ai/code).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

bash "$CLAUDE_PROJECT_DIR/video-editor/setup/install.sh" >/dev/null
