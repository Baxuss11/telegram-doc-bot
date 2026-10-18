#!/usr/bin/env bash
# Установка и обновление бота @OSK_Tech_bot на своём сервере (Ubuntu / Debian).
#
# Первый запуск и каждое обновление — одна и та же команда:
#   curl -fsSL https://raw.githubusercontent.com/Baxuss11/telegram-doc-bot/claude/nice-keller-a2pw6y/deploy/install.sh | sudo bash
#
# Бот ставится как системная служба: запускается вместе с сервером
# и сам перезапускается при сбое.
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Baxuss11/telegram-doc-bot.git}"
BRANCH="${BRANCH:-claude/nice-keller-a2pw6y}"
APP_DIR=/opt/osk-bot
DATA_DIR=/var/lib/osk-bot
ENV_FILE=/etc/osk-bot.env
SERVICE=osk-bot
BOT_USER=oskbot

# Всё внутри функции: при «curl | bash» скрипт читается целиком до запуска,
# и команды вроде apt-get не «съедят» его остаток из stdin.
main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Запустите через sudo." >&2
        exit 1
    fi

    echo "==> Системные пакеты"
    apt-get update -qq
    apt-get install -y -qq python3 python3-venv python3-pip git ca-certificates >/dev/null

    if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
        echo "Нужен Python 3.10 или новее, а установлен $(python3 --version)." >&2
        echo "Обновите систему до Ubuntu 22.04+ / Debian 12+." >&2
        exit 1
    fi

    echo "==> Пользователь $BOT_USER"
    id "$BOT_USER" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$BOT_USER"
    mkdir -p "$DATA_DIR"
    chown "$BOT_USER:$BOT_USER" "$DATA_DIR"
    chmod 700 "$DATA_DIR"

    echo "==> Код бота ($BRANCH)"
    if [ -d "$APP_DIR/.git" ]; then
        git -C "$APP_DIR" fetch -q origin "$BRANCH"
        git -C "$APP_DIR" checkout -q -B "$BRANCH" "origin/$BRANCH"
    else
        git clone -q --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    fi

    echo "==> Библиотеки Python"
    [ -d "$APP_DIR/venv" ] || python3 -m venv "$APP_DIR/venv"
    "$APP_DIR/venv/bin/pip" install -q --upgrade pip
    "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

    if [ ! -f "$ENV_FILE" ]; then
        echo "==> Настройки (сохранятся в $ENV_FILE, потом можно поправить: sudo nano $ENV_FILE)"
        # При запуске через «curl | bash» клавиатура — это /dev/tty, а не stdin.
        ask() { local v; read -r -p "$1: " v </dev/tty; echo "$v"; }
        BOT_TOKEN=$(ask "Токен бота от @BotFather")
        ADMIN_IDS=$(ask "Ваш Telegram ID (несколько — через запятую)")
        APPS_SCRIPT_URL=$(ask "URL веб-приложения Apps Script (можно пусто и добавить позже)")
        APPS_SCRIPT_SECRET=$(ask "Пароль Apps Script (SECRET из Code.gs, можно пусто)")
        umask 077
        cat > "$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
ADMIN_IDS=$ADMIN_IDS
APPS_SCRIPT_URL=$APPS_SCRIPT_URL
APPS_SCRIPT_SECRET=$APPS_SCRIPT_SECRET
DATA_DIR=$DATA_DIR
EOF
        chmod 600 "$ENV_FILE"
    fi

    echo "==> Служба $SERVICE"
    cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Telegram bot @OSK_Tech_bot (технический рейтинг ОСК)
After=network-online.target
Wants=network-online.target

[Service]
User=$BOT_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/venv/bin/python doc_bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable -q "$SERVICE"
    systemctl restart "$SERVICE"
    sleep 5

    if systemctl is-active -q "$SERVICE"; then
        echo
        echo "✅ Бот запущен и будет работать круглосуточно."
        echo "   Логи:        sudo journalctl -u $SERVICE -f"
        echo "   Перезапуск:  sudo systemctl restart $SERVICE"
        echo "   Настройки:   sudo nano $ENV_FILE  (после правки — перезапуск)"
    else
        echo
        echo "⚠️ Бот не запустился. Последние строки лога:"
        journalctl -u "$SERVICE" -n 30 --no-pager
        exit 1
    fi
}

main "$@"
