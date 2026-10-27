"""CLI: собрать и вывести сводный отчёт «расход vs заявки по креативам».

Примеры:
  python -m ad_analytics --days 30
  python -m ad_analytics --since 2026-10-01 --until 2026-10-27 --format json

Все секреты — из окружения (см. ad_analytics/README.md и .env.example).
"""

import argparse
import datetime
import json
import sys

from .bitrix import BitrixError, fetch_items
from .config import Config, ConfigError
from .meta import MetaError, fetch_ad_insights
from .report import build_report, format_text


def _default_dates(days: int):
    until = datetime.date.today()
    since = until - datetime.timedelta(days=days)
    return since.isoformat(), until.isoformat()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ad_analytics",
        description="Сводит расход Meta с заявками Bitrix24 по каждому креативу.",
    )
    parser.add_argument("--days", type=int, default=30, help="период, дней назад от сегодня (по умолчанию 30)")
    parser.add_argument("--since", help="начало периода YYYY-MM-DD (переопределяет --days)")
    parser.add_argument("--until", help="конец периода YYYY-MM-DD")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    try:
        cfg = Config()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    if args.since and args.until:
        since, until = args.since, args.until
    else:
        since, until = _default_dates(args.days)

    try:
        insights = []
        for account_id in cfg.ad_account_ids:
            insights += fetch_ad_insights(
                cfg.meta_token, cfg.api_version, account_id, since, until
            )
    except MetaError as exc:
        print(f"Ошибка Meta: {exc}", file=sys.stderr)
        return 3

    select = ["ID", "TITLE", "DATE_CREATE", cfg.qualified_field] + cfg.lead_key_fields
    try:
        leads = fetch_items(cfg.bitrix_webhook, cfg.entity, since, select)
    except BitrixError as exc:
        print(f"Ошибка Bitrix24: {exc}", file=sys.stderr)
        return 4

    report = build_report(
        insights, leads, cfg.lead_key_fields, cfg.qualified_field, cfg.qualified_values
    )

    if args.format == "json":
        payload = {"since": since, "until": until, **report}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(format_text(report, since, until))

    return 0


if __name__ == "__main__":
    sys.exit(main())
