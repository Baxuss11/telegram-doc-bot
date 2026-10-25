#!/usr/bin/env bash
# Установка всего для автомонтажа: ffmpeg, Python + faster-whisper, Node.js.
# macOS (Homebrew) и Linux (apt). Запуск: bash video-editor/setup/install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null && SUDO="sudo"

have() { command -v "$1" >/dev/null 2>&1; }

install_pkgs() {
  if [ "$(uname)" = "Darwin" ]; then
    if ! have brew; then
      echo "→ ставлю Homebrew"
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
    fi
    brew install "$@"
  elif have apt-get; then
    $SUDO apt-get update -qq && $SUDO apt-get install -y -qq "$@"
  else
    echo "Поставь вручную: $*" >&2
    exit 1
  fi
}

have ffmpeg  || { echo "→ ставлю ffmpeg";  install_pkgs ffmpeg; }
have python3 || { echo "→ ставлю Python"; if [ "$(uname)" = "Darwin" ]; then install_pkgs python; else install_pkgs python3 python3-pip; fi; }
have node    || { echo "→ ставлю Node.js"; if [ "$(uname)" = "Darwin" ]; then install_pkgs node; else install_pkgs nodejs npm; fi; }

if ! python3 -c "import faster_whisper, PIL" 2>/dev/null; then
  echo "→ ставлю faster-whisper"
  python3 -m pip install -q -r "$HERE/requirements.txt" 2>/dev/null \
    || python3 -m pip install -q --user -r "$HERE/requirements.txt" 2>/dev/null \
    || python3 -m pip install -q --break-system-packages -r "$HERE/requirements.txt"
fi

echo
echo "Проверка:"
ffmpeg -version | head -1
FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
grep -q " subtitles " <<<"$FILTERS" && echo "ffmpeg: субтитры (libass) — есть" \
  || echo "⚠️  ffmpeg собран без libass — субтитры не вшить"
python3 --version
python3 -c "import faster_whisper; print('faster-whisper', faster_whisper.__version__)"
echo "node $(node --version)"
echo "Готово ✅"
