"""Утренняя отправка отчёта «расход vs заявки» в Telegram через заданного бота.

Запускается по расписанию (cron). Берёт из окружения:
  BRIEFING_BOT_TOKEN — токен бота, от чьего имени слать (напр. @baktiiar_briefing_bot);
  BRIEFING_CHAT_ID   — кому слать (Telegram ID получателя);
  ADS_REPORT_DAYS    — период отчёта в днях (по умолчанию 7).
Секреты рекламы (META_*, BITRIX_*) берутся так же, как и для /ads.
"""

import html
import os
import sys

import requests

from .report import format_text
from .service import generate_report


def _split(text, limit=3500):
    chunks, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        chunks.append(cur)
    return chunks


def main() -> int:
    token = (os.environ.get("BRIEFING_BOT_TOKEN") or "").strip()
    chat_id = (os.environ.get("BRIEFING_CHAT_ID") or "").strip()
    if not token or not chat_id:
        print("Не заданы BRIEFING_BOT_TOKEN и/или BRIEFING_CHAT_ID", file=sys.stderr)
        return 2

    days = int(os.environ.get("ADS_REPORT_DAYS", "7"))

    report, since, until = generate_report(days=days)
    text = "📊 Отчёт по рекламе (расход vs заявки)\n\n" + format_text(report, since, until)

    api = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in _split(text):
        resp = requests.post(
            api,
            data={"chat_id": chat_id, "text": f"<pre>{html.escape(chunk)}</pre>",
                  "parse_mode": "HTML"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"Telegram вернул {resp.status_code}: {resp.text[:300]}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
