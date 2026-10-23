#!/usr/bin/env bash
# Установка публичного бота для покупателей (@OtzyvSK_bot) ВТОРОЙ службой
# на том же сервере, рядом с рабочим @OSK_Tech_bot.
#
# Сначала должен быть установлен основной бот (deploy/install.sh) — публичный
# использует тот же код в /opt/osk-bot, но свой токен, свою базу и свою службу.
#
#   curl -fsSL https://raw.githubusercontent.com/Baxuss11/telegram-doc-bot/claude/nice-keller-a2pw6y/deploy/install-public.sh | sudo bash -s -- 'ТОКЕН_OTZYV' 'URL_APPS_SCRIPT' 'ПАРОЛЬ_APPS_SCRIPT' 'https://wa.me/996XXXXXXXXX'
#
# Аргументы: 1 — токен @OtzyvSK_bot (обязательно); 2 — URL Apps Script;
# 3 — пароль Apps Script; 4 — ссылка «Связаться» (WhatsApp/Telegram, необязательно).
set -euo pipefail

APP_DIR=/opt/osk-bot
DATA_DIR=/var/lib/osk-bot-public
ENV_FILE=/etc/osk-bot-public.env
SERVICE=osk-bot-public
BOT_USER=oskbot

main() {
    if [ "$(id -u)" -ne 0 ]; then
        echo "Запустите через sudo." >&2
        exit 1
    fi
    if [ ! -x "$APP_DIR/venv/bin/python" ]; then
        echo "Сначала установите основной бот (deploy/install.sh) — нет $APP_DIR/venv." >&2
        exit 1
    fi

    BOT_TOKEN="${1:-}"
    APPS_SCRIPT_URL="${2:-}"
    APPS_SCRIPT_SECRET="${3:-}"
    SUPPORT_CONTACT="${4:-}"
    if [ -z "$BOT_TOKEN" ]; then
        echo "⚠️ Укажите токен @OtzyvSK_bot первым аргументом." >&2
        exit 1
    fi

    # Если Apps Script не передали — возьмём из настроек основного бота.
    if [ -z "$APPS_SCRIPT_URL" ] && [ -f /etc/osk-bot.env ]; then
        APPS_SCRIPT_URL=$(grep -E '^APPS_SCRIPT_URL=' /etc/osk-bot.env | cut -d= -f2- || true)
        APPS_SCRIPT_SECRET=$(grep -E '^APPS_SCRIPT_SECRET=' /etc/osk-bot.env | cut -d= -f2- || true)
    fi

    echo "==> База публичного бота"
    mkdir -p "$DATA_DIR"
    chown "$BOT_USER:$BOT_USER" "$DATA_DIR"
    chmod 700 "$DATA_DIR"

    echo "==> Настройки → $ENV_FILE"
    umask 077
    cat > "$ENV_FILE" <<EOF
BOT_TOKEN=$BOT_TOKEN
PUBLIC_MODE=1
APPS_SCRIPT_URL=$APPS_SCRIPT_URL
APPS_SCRIPT_SECRET=$APPS_SCRIPT_SECRET
SUPPORT_CONTACT=$SUPPORT_CONTACT
DATA_DIR=$DATA_DIR
EOF
    chmod 600 "$ENV_FILE"

    echo "==> Служба $SERVICE"
    cat > "/etc/systemd/system/$SERVICE.service" <<EOF
[Unit]
Description=Telegram bot @OtzyvSK_bot (публичная проверка документов ОСК)
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
        echo "✅ Публичный бот @OtzyvSK_bot запущен."
        echo "   Логи:       sudo journalctl -u $SERVICE -f"
        echo "   Перезапуск: sudo systemctl restart $SERVICE"
    else
        echo
        echo "⚠️ Не запустился. Последние строки лога:"
        journalctl -u "$SERVICE" -n 30 --no-pager
        exit 1
    fi
}

main "$@"
