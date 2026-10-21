#!/bin/bash
# Сборка LayoutSwitcher.app (запускается на Mac, в том числе в GitHub Actions).
# Нужен Python с python.org (universal2), чтобы приложение работало и на Intel, и на Apple Silicon.
set -euo pipefail
cd "$(dirname "$0")"
PYTHON=${PYTHON:-python3}

"$PYTHON" -m pip install --quiet -r requirements.txt pyinstaller
rm -rf build dist
"$PYTHON" -m PyInstaller --noconfirm --windowed --name LayoutSwitcher \
    --target-arch "${ARCH:-universal2}" \
    --osx-bundle-identifier io.github.baxuss11.layoutswitcher \
    --add-data data:data --add-data exceptions.txt:. \
    --hidden-import ServiceManagement \
    mac_app.py

PLIST=dist/LayoutSwitcher.app/Contents/Info.plist
plutil -replace LSUIElement -bool true "$PLIST"   # только значок в строке меню, без Dock
plutil -replace CFBundleDisplayName -string 'Переключатель раскладки' "$PLIST"
codesign --force --deep --sign - dist/LayoutSwitcher.app
(cd dist && ditto -c -k --keepParent LayoutSwitcher.app LayoutSwitcher.zip)
echo "Готово: dist/LayoutSwitcher.app, dist/LayoutSwitcher.zip"
