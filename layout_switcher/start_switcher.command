#!/bin/bash
# Запуск переключателя на Mac: двойной щелчок по этому файлу
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  echo "Первый запуск: устанавливаю зависимости..."
  if ! python3 -m venv .venv; then
    echo "Нужен Python 3: https://www.python.org/downloads/"
    read -r -p "Нажмите Enter, чтобы закрыть"
    exit 1
  fi
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
.venv/bin/python switcher.py
read -r -p "Нажмите Enter, чтобы закрыть"
