#!/bin/bash
# Установка переключателя раскладки на Mac одной командой:
#   curl -fsSL https://raw.githubusercontent.com/Baxuss11/telegram-doc-bot/claude/confident-davinci-7fekcs/layout_switcher/install_mac.sh | bash
set -euo pipefail
APP=LayoutSwitcher.app
BUNDLE_ID=io.github.baxuss11.layoutswitcher
URL=https://github.com/Baxuss11/telegram-doc-bot/releases/download/layout-switcher-latest/LayoutSwitcher.zip

DEST=/Applications
if [ ! -w "$DEST" ]; then
  DEST="$HOME/Applications"
  mkdir -p "$DEST"
fi

echo "⬇️  Скачиваю переключатель раскладки..."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
curl -fL --progress-bar "$URL" -o "$TMP/app.zip"

pkill -x LayoutSwitcher 2>/dev/null || true
rm -rf "${DEST:?}/$APP"
ditto -x -k "$TMP/app.zip" "$DEST"
xattr -dr com.apple.quarantine "$DEST/$APP" 2>/dev/null || true
# Разрешения от прошлой версии не подходят к новой — сбрасываем, macOS спросит заново
tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true
tccutil reset ListenEvent "$BUNDLE_ID" >/dev/null 2>&1 || true

echo "✅ Установлено: $DEST/$APP"
echo "   Сейчас приложение откроется и попросит доступ к клавиатуре."
echo "   Включите «LayoutSwitcher» в «Универсальном доступе» и «Мониторинге ввода»."
open "$DEST/$APP"
